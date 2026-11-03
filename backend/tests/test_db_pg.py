"""The Postgres query layer: what SQL it writes, what it refuses, and (with RUN_DB_TESTS=1) that it behaves like PostgREST."""
import os
import uuid

import pytest
from postgrest.exceptions import APIError
from psycopg.types.json import Jsonb

from app.db_pg import PgDB, Query


class Stub(PgDB):  # builds queries without connecting
    def __init__(self):
        self.ran = []

    types: dict = {}

    def column_types(self, table):
        return self.types

    def run(self, sql, params, fetch=True, retry=False):
        self.ran.append((sql, params, retry))
        return []


def q(table="sessions"):
    return Query(Stub(), table)


# ================================================================ reads
def test_select_with_filters_order_and_limit():
    sql, p = q("slots").select("*").eq("provider_id", "pl_01").eq("status", "free").gte("start_at", "2026-11-03T00:00:00") \
        .lt("start_at", "2026-11-04").order("start_at").limit(60).build()
    assert sql == ('select to_jsonb(t) from (select * from "slots" where "provider_id" = %s and "status" = %s and '
                   '"start_at" >= %s and "start_at" < %s order by "start_at" asc limit 60) t')
    assert p == ["pl_01", "free", "2026-11-03T00:00:00", "2026-11-04"]


def test_columns_are_quoted_and_descending_order_works():
    sql, _ = q("messages").select("seq, role").eq("session_id", "s").order("seq", desc=True).build()
    assert 'select "seq", "role" from "messages"' in sql and 'order by "seq" desc' in sql


def test_is_null_in_list_ilike_and_json_key_filters():
    sql, p = q("events").select("*").is_("read_at", "null").in_("id", ["a", "b", "c"]).ilike("area", "indira%") \
        .filter("payload->>user_id", "eq", "u1").build()
    assert '"read_at" is null' in sql and '"id" in (%s, %s, %s)' in sql and '"area" ilike %s' in sql
    assert """"payload"->>'user_id' = %s""" in sql and p == ["a", "b", "c", "indira%", "u1"]


def test_an_empty_in_list_matches_nothing():
    sql, p = q().select("*").in_("id", []).build()
    assert "where false" in sql and p == []


def test_postgrest_or_expressions_are_parsed():
    sql, p = q("bookings").select("*").or_("hold_id.eq.h1,idempotency_key.eq.k1").build()
    assert '("hold_id" = %s or "idempotency_key" = %s)' in sql and p == ["h1", "k1"]


def test_only_selects_are_retried_when_a_connection_went_stale():
    db = Stub()
    Query(db, "slots").select("*").execute()
    Query(db, "slots").update({"status": "free"}).eq("id", "x").execute()
    assert [r[2] for r in db.ran] == [True, False]


# ================================================================ writes
def test_insert_returns_the_row_and_wraps_documents_as_jsonb():
    sql, p = q("sessions").insert({"user_id": "u", "request_frame": {"a": 1}}).build()
    assert sql == ('with q as (insert into "sessions" ("user_id", "request_frame") values (%s, %s) returning *) '
                   'select to_jsonb(q) from q')
    assert p[0] == "u" and isinstance(p[1], Jsonb)


def test_multi_row_insert_uses_default_for_columns_a_row_omits():
    sql, p = q("trace_spans").insert([{"seq": 0, "name": "a"}, {"seq": 1}]).build()
    assert "values (%s, %s), (%s, default)" in sql and p == [0, "a", 1]


def test_upsert_ignoring_duplicates_returns_only_rows_that_were_new():
    sql, _ = q("events").upsert({"dedupe_key": "k", "type": "t"}, on_conflict="dedupe_key", ignore_duplicates=True).build()
    assert 'on conflict ("dedupe_key") do nothing returning *' in sql


def test_upsert_updates_the_other_columns_on_conflict():
    sql, _ = q("idempotency_keys").upsert({"key": "k", "tool": "counter", "response": {"n": 1}}, on_conflict="key").build()
    assert 'on conflict ("key") do update set "tool" = excluded."tool", "response" = excluded."response"' in sql


def test_update_and_delete_return_the_affected_rows():
    sql, p = q("events").update({"status": "processing"}).eq("id", "e1").eq("status", "pending").build()
    assert sql == ('with q as (update "events" set "status" = %s where "id" = %s and "status" = %s returning *) '
                   'select to_jsonb(q) from q') and p == ["processing", "e1", "pending"]
    sql, p = q("memories").delete().eq("user_id", "u").build()
    assert sql.startswith('with q as (delete from "memories" where "user_id" = %s returning *)')


def test_a_plain_value_going_into_a_jsonb_column_is_stored_as_a_json_document():
    db = Stub()
    db.types = {"value": "jsonb", "key": "text", "tags": "ARRAY"}
    _, p = Query(db, "memories").insert({"key": "preferred_area", "value": "Indiranagar"}).build()
    assert p[0] == "preferred_area" and isinstance(p[1], Jsonb) and p[1].obj == "Indiranagar"
    _, p = Query(db, "memories").update({"value": 1500}).eq("id", "m").build()
    assert isinstance(p[0], Jsonb) and p[0].obj == 1500
    _, p = Query(db, "memories").update({"value": True}).eq("id", "m").build()
    assert p[0].obj is True
    _, p = Query(db, "x").insert({"tags": ["a", "b"], "value": None}).build()
    assert p == [["a", "b"], None]  # a Postgres array stays an array; NULL stays NULL


# ================================================================ refusals
def test_update_and_delete_without_a_filter_are_refused():
    with pytest.raises(ValueError, match="without a filter"):
        q("slots").update({"status": "free"}).build()
    with pytest.raises(ValueError, match="without a filter"):
        q("slots").delete().build()


@pytest.mark.parametrize("bad", ['id"; drop table users; --', "a b", "1abc", "", "x.y", "payload->>"])
def test_hostile_identifiers_never_reach_the_sql(bad):
    with pytest.raises(ValueError):
        q().select("*").eq(bad, 1).build()
    with pytest.raises(ValueError):
        q().select(bad).build()
    with pytest.raises(ValueError):
        Query(Stub(), bad)


def test_values_are_always_bound_parameters_never_part_of_the_sql():
    evil = "x' or '1'='1"
    sql, p = q().select("*").eq("user_id", evil).build()
    assert evil not in sql and p == [evil]


# ================================================================ against the real database (RUN_DB_TESTS=1)
real = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="RUN_DB_TESTS!=1")


@pytest.fixture(scope="module")
def db():
    from app.config import get_settings
    from app.db_pg import get_pg
    return get_pg(get_settings().supabase_db_url)


@real
def test_rows_look_like_postgrest_rows(db):
    row = db.table("providers").select("*").order("id").limit(1).execute().data[0]
    assert isinstance(row["id"], str) and isinstance(row["rating"], (int, float)) and isinstance(row["attributes"], dict)
    slot = db.table("slots").select("*").limit(1).execute().data[0]
    assert isinstance(slot["start_at"], str) and slot["start_at"].endswith("+00:00")  # ISO string, UTC


@real
def test_an_insert_round_trips_and_cleans_up(db):
    uid = f"pgtest_{uuid.uuid4().hex[:8]}"
    try:
        db.table("users").upsert({"id": uid, "name": uid}, on_conflict="id", ignore_duplicates=True).execute()
        again = db.table("users").upsert({"id": uid, "name": uid}, on_conflict="id", ignore_duplicates=True).execute().data
        assert again == []  # a duplicate inserts nothing, like PostgREST
        s = db.table("sessions").insert({"user_id": uid, "state": "INTAKE", "request_frame": {"service": "plumber"}}).execute().data[0]
        assert s["request_frame"] == {"service": "plumber"} and s["version"] == 0
        db.table("sessions").update({"state": "PICK", "request_frame": {"x": [1, 2]}}).eq("id", s["id"]).execute()
        got = db.table("sessions").select("*").eq("id", s["id"]).limit(1).execute().data[0]
        assert got["state"] == "PICK" and got["request_frame"] == {"x": [1, 2]} and got["version"] == 1  # trigger ran
    finally:
        db.table("sessions").delete().eq("user_id", uid).execute()
        db.table("users").delete().eq("id", uid).execute()


@real
def test_every_kind_of_function_result_matches_what_the_stores_expect(db):
    uid = f"pgtest_{uuid.uuid4().hex[:8]}"
    try:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        db.table("users").upsert({"id": uid, "name": uid}, on_conflict="id", ignore_duplicates=True).execute()
        got = db.rpc("acquire_user_lock", {"p_user": uid, "p_now": now.isoformat(), "p_until": (now + timedelta(seconds=30)).isoformat()}).execute().data
        again = db.rpc("acquire_user_lock", {"p_user": uid, "p_now": now.isoformat(), "p_until": (now + timedelta(seconds=30)).isoformat()}).execute().data
        assert got is True and again is False  # a boolean comes back as a boolean
        assert isinstance(db.rpc("close_stale_runs", {"p_now": now.isoformat()}).execute().data, int)  # a scalar int
        assert db.rpc("unresolved_attempts", {"p_user": uid}).execute().data == []  # setof: a list
        assert db.rpc("list_conversations", {"p_user": uid, "p_limit": 5, "p_before": None}).execute().data == []  # table: a list
    finally:
        db.table("users").delete().eq("id", uid).execute()


@real
def test_database_errors_surface_as_postgrest_api_errors(db):
    with pytest.raises(APIError) as e:
        db.table("sessions").select("*").eq("id", "not-a-uuid").execute()
    assert e.value.code == "22P02"  # the same code the stores already look for
    with pytest.raises(APIError):
        db.table("no_such_table").select("*").execute()
