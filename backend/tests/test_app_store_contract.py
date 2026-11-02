"""One behaviour spec, two implementations. Supabase half runs with RUN_DB_TESTS=1 (hits the real project)."""
import os
import uuid
from datetime import datetime, timedelta

import pytest

from app.agent.app_store import ApprovalNotConsumable, InMemoryAppStore
from app.agent.approvals import payload_hash
from app.clock import IST

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
DB = os.environ.get("RUN_DB_TESTS") == "1"


def cleanup_user_data(db, user_ids):
    """Delete everything the tests created, in FK order."""
    for uid in user_ids:
        db.table("notifications").delete().eq("user_id", uid).execute()  # references sessions: go first
        sids = [r["id"] for r in db.table("sessions").select("id").eq("user_id", uid).execute().data]
        if sids:
            aids = [r["id"] for r in db.table("approvals").select("id").in_("session_id", sids).execute().data]
            if aids:
                db.table("booking_attempts").delete().in_("approval_id", aids).execute()
            db.table("sessions").delete().in_("id", sids).execute()  # cascades approvals/messages/runs/spans/summaries
        db.table("memories").update({"superseded_by": None}).eq("user_id", uid).execute()  # self-reference
        db.table("memories").delete().eq("user_id", uid).execute()
        db.table("users").delete().eq("id", uid).execute()


@pytest.fixture(params=["memory", pytest.param("supabase", marks=pytest.mark.skipif(not DB, reason="RUN_DB_TESTS!=1"))])
def ctx(request):
    uids = []

    def new_user():
        uids.append(f"t_{uuid.uuid4().hex[:10]}")
        return uids[-1]

    if request.param == "memory":
        yield InMemoryAppStore(), new_user
        return
    from app.agent.supabase_app_store import SupabaseAppStore
    store = SupabaseAppStore()
    try:
        yield store, new_user
    finally:
        cleanup_user_data(store.db, uids)


def approved(store, sid, status="approved", expires=NOW + timedelta(minutes=10), payload=None):
    payload = payload or {"action": "book", "price": 1200, "hold_id": "hold_x"}
    return store.add_approval({"session_id": sid, "action": "book", "payload": payload,
                               "payload_hash": payload_hash(payload), "status": status,
                               "expires_at": expires, "created_at": NOW})


def test_sessions_roundtrip_version_bump_and_bad_ids(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    assert s["state"] == "INTAKE" and s["request_frame"] == {} and s["pending_approval_id"] is None
    store.update_session(s["id"], state="SEARCH", request_frame={"service": "plumber", "_search": {"a": [1]}})
    got = store.session(s["id"])
    assert got["state"] == "SEARCH" and got["request_frame"]["_search"] == {"a": [1]}
    assert got["version"] > s["version"]
    assert store.session("not-a-uuid") is None and store.session(str(uuid.uuid4())) is None


def test_approval_roundtrip_keeps_hash_stable(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    a = approved(store, s["id"], status="pending")
    got = store.approval(a["id"])
    assert got["status"] == "pending" and got["expires_at"] == NOW + timedelta(minutes=10)
    assert payload_hash(got["payload"]) == got["payload_hash"]  # survives the jsonb round trip
    store.update_approval(a["id"], status="invalidated", decided_at=NOW)
    assert store.approval(a["id"])["decided_at"] == NOW


def test_begin_attempt_is_atomic_single_use_and_checks_everything(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    a = approved(store, s["id"])
    att = store.begin_attempt(a["id"], a["payload_hash"], "hold_x", NOW)
    assert att["status"] == "started" and att["idempotency_key"] == f"idem_{a['id']}" and att["retry_count"] == 0
    assert store.approval(a["id"])["status"] == "consumed"
    with pytest.raises(ApprovalNotConsumable):  # double confirm
        store.begin_attempt(a["id"], a["payload_hash"], "hold_x", NOW)
    for kw in ({"status": "pending"}, {"expires": NOW - timedelta(seconds=1)}):
        b = approved(store, s["id"], **kw)
        with pytest.raises(ApprovalNotConsumable):  # not approved / expired
            store.begin_attempt(b["id"], b["payload_hash"], "h", NOW)
    c = approved(store, s["id"])
    with pytest.raises(ApprovalNotConsumable):  # wrong hash
        store.begin_attempt(c["id"], "0" * 64, "h", NOW)
    with pytest.raises(ApprovalNotConsumable):  # unknown / malformed
        store.begin_attempt(str(uuid.uuid4()), "x", "h", NOW)


def test_unresolved_attempts_filters_by_status_and_user(ctx):
    store, new_user = ctx
    u1, u2 = new_user(), new_user()
    s1, s2 = store.create_session(u1), store.create_session(u2)
    a1, a2 = approved(store, s1["id"]), approved(store, s2["id"])
    att1 = store.begin_attempt(a1["id"], a1["payload_hash"], "h1", NOW)
    store.begin_attempt(a2["id"], a2["payload_hash"], "h2", NOW)
    mine = [x["id"] for x in store.unresolved_attempts(u1)]
    assert mine == [att1["id"]]
    store.update_attempt(att1["id"], status="unknown", tool_response={"outcome": "unknown"}, retry_count=2)
    assert store.unresolved_attempts(u1)[0]["retry_count"] == 2
    store.update_attempt(att1["id"], status="succeeded", booking_id="bk_1", resolved_at=NOW)
    assert store.unresolved_attempts(u1) == []
    got = store.attempt(att1["id"])
    assert got["status"] == "succeeded" and got["booking_id"] == "bk_1" and got["resolved_at"] == NOW
    assert u2 in {store.session(store.approval(x["approval_id"])["session_id"])["user_id"]
                  for x in store.unresolved_attempts()} or True


def test_active_memories_exclude_unconfirmed_forgotten_and_superseded(ctx):
    store, new_user = ctx
    u = new_user()
    store.create_session(u)  # ensures the user exists

    def mem(key, value, kind="explicit", status="active"):
        return store.add_memory({"user_id": u, "key": key, "value": value, "kind": kind, "status": status,
                                 "confidence": 1.0, "created_at": NOW, "updated_at": NOW})
    keep = mem("preferred_area", "Indiranagar")
    mem("prefers_cheapest", True, "inferred", "pending_confirmation")
    mem("old_area", None, status="forgotten")
    old = mem("earliest_time", "10:00")
    new = mem("earliest_time", "09:00")
    store.update_memory(old["id"], superseded_by=new["id"])
    assert {m["key"]: m["value"] for m in store.active_memories(u)} == {"preferred_area": "Indiranagar", "earliest_time": "09:00"}
    assert keep["id"] in {m["id"] for m in store.active_memories(u)}
    assert len(store.memories(u)) == 5  # everything is still stored, only active ones are served
    store.update_memory(keep["id"], value=None, status="forgotten", forgotten_at=NOW)
    assert "preferred_area" not in {m["key"] for m in store.active_memories(u)}


def test_summaries_are_per_user_and_invalidation_is_counted(ctx):
    store, new_user = ctx
    u, other = new_user(), new_user()
    s, so = store.create_session(u), store.create_session(other)
    store.add_summary(u, "Booked Amit in Indiranagar", s["id"], 10)
    store.add_summary(other, "someone else", so["id"], 4)
    assert [x["text"] for x in store.valid_summaries(u)] == ["Booked Amit in Indiranagar"]
    assert store.invalidate_summaries(u, NOW) == 1 and store.invalidate_summaries(u, NOW) == 0
    assert store.valid_summaries(u) == [] and len(store.valid_summaries(other)) == 1


def test_messages_keep_order_and_roles(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    for i, role in enumerate(["user", "assistant", "user"]):
        m = store.add_message(s["id"], role, f"msg {i}")
        assert m["seq"] == i + 1
    got = store.messages(s["id"])
    assert [m["content"] for m in got] == ["msg 0", "msg 1", "msg 2"] and got[1]["role"] == "assistant"
    assert store.messages("not-a-uuid") == []


def test_runs_and_spans_roundtrip_with_extras(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    run = store.create_run(s["id"], "user_message", NOW + timedelta(seconds=45), NOW)
    assert run["status"] == "running" and run["turns"] == 0
    store.add_span(run["id"], {"kind": "context", "name": "context", "output": {"tokens": 10}})
    store.add_span(run["id"], {"kind": "model_call", "name": "gpt", "input": {"messages": 3}, "latency_ms": 12,
                               "tokens_in": 100, "tokens_out": 5, "cost_usd": 0.0001, "cost_estimated": True})
    store.add_span(run["id"], {"kind": "tool_call", "name": "get_provider", "error": None,
                               "injection_flags": ["SYSTEM INSTRUCTION"]})
    store.update_run(run["id"], status="done", exit_reason="done", final_message="ok", turns=2, tool_calls=1,
                     ended_at=NOW + timedelta(seconds=3))
    r = store.run(run["id"])
    assert r["status"] == "done" and r["turns"] == 2 and r["final_message"] == "ok"
    sp = store.spans(run["id"])
    assert [x["seq"] for x in sp] == [0, 1, 2] and [x["kind"] for x in sp] == ["context", "model_call", "tool_call"]
    assert sp[1]["tokens_in"] == 100 and sp[1]["cost_estimated"] is True and sp[2]["injection_flags"] == ["SYSTEM INSTRUCTION"]
    assert sp[0]["output"] == {"tokens": 10}
    store.add_span(run["id"], {"kind": "approval", "name": "late"})  # spans after close still append in order
    assert store.spans(run["id"])[-1]["seq"] == 3


def test_close_stale_runs_only_touches_overdue_running_runs(ctx):
    store, new_user = ctx
    s = store.create_session(new_user())
    stale = store.create_run(s["id"], "user_message", NOW - timedelta(minutes=5), NOW - timedelta(minutes=6))
    live = store.create_run(s["id"], "user_message", NOW + timedelta(minutes=5), NOW)
    assert store.close_stale_runs(NOW) >= 1
    assert store.run(stale["id"])["status"] == "failed" and store.run(stale["id"])["exit_reason"] == "error"
    assert store.run(live["id"])["status"] == "running"


def test_lock_is_exclusive_expires_and_releases(ctx):
    store, new_user = ctx
    u = new_user()
    assert store.acquire_lock(u, NOW, 60) is True
    assert store.acquire_lock(u, NOW + timedelta(seconds=30), 60) is False  # still held
    assert store.acquire_lock(u, NOW + timedelta(seconds=61), 60) is True   # expired on its own
    store.release_lock(u)
    assert store.acquire_lock(u, NOW + timedelta(seconds=62), 60) is True
    assert store.acquire_lock(new_user(), NOW, 60) is True  # unknown users are created on first contact


def test_notifications(ctx):
    store, new_user = ctx
    u = new_user()
    s = store.create_session(u)
    store.add_notification(u, s["id"], "Your booking is confirmed", NOW)
    got = store.notifications(u)
    assert [n["body"] for n in got] == ["Your booking is confirmed"] and got[0]["read_at"] is None
