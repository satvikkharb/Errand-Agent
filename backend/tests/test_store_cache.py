"""While a request holds the per-user lock, reads are cached and session writes are sent once, on release.

Round trips are the dominant cost of the Supabase backend (~200 ms each), so these tests count them with a fake
client, and also prove the cache can never hand out a stale or shared row.
"""
import os
import threading

import pytest

from app.agent import supabase_app_store as mod
from app.agent.supabase_app_store import SupabaseAppStore
from app.clock import IST
from datetime import datetime
from tests.support.fake_db import FakeDB

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=IST)


@pytest.fixture
def env():
    db = FakeDB()
    return db, SupabaseAppStore(db=db)


def locked(store, user="u1"):
    assert store.acquire_lock(user, NOW)
    return user


def test_repeated_reads_inside_the_lock_cost_one_round_trip_each(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.add_message(sid, "user", "hi")
    st.add_memory({"user_id": u, "key": "language", "value": "Hindi", "kind": "explicit", "status": "active"})
    db.log.clear()
    for _ in range(10):
        st.session(sid), st.messages(sid), st.memories(u), st.active_memories(u), st.valid_summaries(u)
    assert db.count("select", "sessions") == 1  # the user's session ids, once (the session row itself is cached from create)
    assert db.count("select", "messages") == 0 and db.count("select", "memories") == 2  # history was cached by add_message
    assert db.count("select", "conversation_summaries") == 1 and len(db.log) <= 6


def test_without_a_lock_nothing_is_cached(env):
    db, st = env
    sid = st.create_session("u1")["id"]
    db.log.clear()
    st.session(sid), st.session(sid), st.session(sid)
    assert db.count("select", "sessions") == 3  # read-only endpoints always see fresh data


def test_session_updates_are_visible_at_once_but_sent_to_the_database_once_on_release(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    db.log.clear()
    st.update_session(sid, state="SEARCH")
    st.update_session(sid, request_frame={"service": "plumber"})
    st.update_session(sid, state="PICK")
    got = st.session(sid)
    assert got["state"] == "PICK" and got["request_frame"] == {"service": "plumber"}  # the next read sees every change
    assert db.count("update", "sessions") == 0  # nothing sent yet
    st.release_lock(u)
    assert db.count("update", "sessions") == 1  # ONE patch for three updates
    row = db.tables["sessions"][0]
    assert row["state"] == "PICK" and row["request_frame"] == {"service": "plumber"}


def test_updates_made_outside_a_lock_are_written_immediately(env):
    db, st = env
    sid = st.create_session("u1")["id"]
    st.update_session(sid, state="DONE")
    assert db.tables["sessions"][0]["state"] == "DONE"


def test_adding_messages_needs_no_lookup_of_the_last_sequence_number(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.messages(sid)
    db.log.clear()
    a, b, c = (st.add_message(sid, "user", t) for t in "abc")
    assert (a["seq"], b["seq"], c["seq"]) == (1, 2, 3)
    assert db.count("select") == 0 and db.count("insert", "messages") == 3  # inserts only
    assert [m["content"] for m in st.messages(sid)] == ["a", "b", "c"] and db.count("select") == 0


def test_the_first_message_in_a_request_fetches_history_once_and_serves_every_later_read(env):
    db, st = env
    sid = st.create_session("u1")["id"]
    st.add_message(sid, "user", "earlier")
    u = locked(st)
    db.log.clear()
    st.add_message(sid, "user", "now")
    st.messages(sid), st.messages(sid)
    assert db.count("select", "messages") == 1
    assert [m["seq"] for m in st.messages(sid)] == [1, 2]


def test_writes_to_memories_and_summaries_invalidate_their_cached_reads(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    assert st.active_memories(u) == []
    m = st.add_memory({"user_id": u, "key": "language", "value": "Hindi", "kind": "explicit", "status": "active",
                       "superseded_by": None})
    assert [x["value"] for x in st.active_memories(u)] == ["Hindi"]
    st.update_memory(m["id"], status="forgotten")
    assert st.active_memories(u) == []
    assert st.valid_summaries(u) == []
    s = st.add_summary(u, "they want a plumber", sid, 4)
    assert [x["text"] for x in st.valid_summaries(u)] == ["they want a plumber"]
    st.invalidate_summary(s["id"], NOW)
    assert st.valid_summaries(u) == []


def test_a_new_conversation_shows_up_in_the_users_session_ids(env):
    db, st = env
    u = locked(st)
    st.create_session(u)
    assert len(st._session_ids(u)) == 1
    st.create_session(u)
    assert len(st._session_ids(u)) == 2


def test_callers_get_copies_so_editing_a_result_cannot_poison_the_cache(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.session(sid)["request_frame"]["injected"] = "x"
    st.messages(sid).append({"seq": 99})
    assert "injected" not in st.session(sid)["request_frame"] and st.messages(sid) == []


def test_titles_and_deletes_go_through_the_same_cached_path(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.set_title(sid, "AC servicing", "llm")
    st.soft_delete_session(sid, NOW)
    got = st.session(sid)
    assert got["title"] == "AC servicing" and got["title_source"] == "llm" and got["deleted_at"] == NOW
    st.release_lock(u)
    row = db.tables["sessions"][0]
    assert row["title"] == "AC servicing" and row["deleted_at"].startswith("2026-11-03")


def test_the_lock_is_released_even_if_sending_the_session_changes_fails(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.update_session(sid, state="PICK")
    db.fail[("update", "sessions")] = True
    with pytest.raises(ConnectionError):
        st.release_lock(u)
    assert db.count("update", "users") == 1  # the user is never left locked out
    assert st._scope() is None  # and the cache is gone


def test_a_failed_lock_acquisition_never_inherits_a_previous_cache(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.session(sid)
    db.rpc = lambda name, args=None: type("R", (), {"execute": lambda s: type("D", (), {"data": False})()})()
    assert st.acquire_lock(u, NOW) is False
    assert st._scope() is None


def test_a_forgotten_release_cannot_serve_stale_rows_forever(env, monkeypatch):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.session(sid)
    db.tables["sessions"][0]["state"] = "CHANGED_BY_SOMEONE_ELSE"
    clock = {"t": mod.time.monotonic()}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"] + mod.SCOPE_MAX_S + 1)
    assert st.session(sid)["state"] == "CHANGED_BY_SOMEONE_ELSE"  # the scope expired, so this was a real read


def test_each_thread_has_its_own_scope(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.session(sid)
    seen = {}
    t = threading.Thread(target=lambda: seen.update(scope=st._scope()))
    t.start(), t.join()
    assert seen["scope"] is None and st._scope() is not None


def test_listing_conversations_sends_pending_changes_first(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    st.update_session(sid, title="Fix tap")
    st.list_conversations(u)
    assert db.tables["sessions"][0]["title"] == "Fix tap"


def test_a_new_run_never_asks_the_database_for_its_span_sequence(env):
    db, st = env
    u = locked(st)
    sid = st.create_session(u)["id"]
    run = st.create_run(sid, "user_message", NOW, NOW)
    st.add_span(run["id"], {"kind": "context", "name": "x"})
    db.log.clear()
    st.update_run(run["id"], ended_at=NOW)
    assert db.count("select", "trace_spans") == 0 and db.count("insert", "trace_spans") == 1


def test_provider_reference_data_is_cached_for_a_few_minutes(env):
    from app.tools.supabase_store import SupabaseStore
    db = FakeDB()
    db.tables["providers"] = [{"id": "p1", "area": "Indiranagar"}, {"id": "p2", "area": "Koramangala"}]
    ps = SupabaseStore(db=db)
    assert ps.areas() == ["Indiranagar", "Koramangala"] and ps.areas() == ["Indiranagar", "Koramangala"]
    ps.provider("p1"), ps.provider("p1")
    assert db.count("select", "providers") == 2  # one for areas, one for provider p1


@pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="RUN_DB_TESTS!=1")
def test_a_real_chat_turn_stays_inside_its_round_trip_budget():
    """On the real database a whole chat turn used to take 49 calls (~9 s). Keep it well under 30."""
    import uuid

    import httpx
    from app.agent.orchestrator import Orchestrator
    from app.clock import FixedClock
    from app.llm import ScriptedLLM
    from app.tools.supabase_store import SupabaseStore
    from tests.test_app_store_contract import cleanup_user_data
    from tests.test_orchestrator import FIRST_MSG, find_steps

    calls = []
    orig = httpx.Client.send
    httpx.Client.send = lambda self, req, *a, **k: (calls.append(req.url.path), orig(self, req, *a, **k))[1]
    user = f"cache_{uuid.uuid4().hex[:8]}"
    try:
        o = Orchestrator(SupabaseAppStore(), SupabaseStore(), FixedClock(NOW), ScriptedLLM(find_steps()))
        calls.clear()
        o.handle_message(user, FIRST_MSG)
        assert len(calls) <= 30, f"{len(calls)} round trips: {sorted(set(calls))}"
    finally:
        httpx.Client.send = orig
        cleanup_user_data(SupabaseAppStore().db, user)
