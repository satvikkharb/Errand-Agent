"""End-to-end on the REAL Supabase project (RUN_DB_TESTS=1): persistence, restart, concurrency.

The scripted model drives the chat; everything else (stores, atomic functions, locks, executor) is real.
Each test uses a throwaway user and restores the provider world it touched.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime

import pytest

from app.agent import approvals
from app.agent.orchestrator import Orchestrator
from app.agent.supabase_app_store import SupabaseAppStore
from app.agent.trace import NullTracer
from app.clock import IST, FixedClock
from app.db import get_db
from app.llm import ScriptedLLM, call
from app.tools.config import ScenarioConfig
from app.tools.supabase_store import SupabaseStore
from tests.test_app_store_contract import cleanup_user_data
from tests.test_orchestrator import FIRST_MSG, find_steps

pytestmark = pytest.mark.skipif(os.environ.get("RUN_DB_TESTS") != "1", reason="RUN_DB_TESTS!=1")
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)


def process(llm_steps=(), forced=("success",)):
    """A fresh 'server process': new store objects, new orchestrator. Shares only the database."""
    store, app = SupabaseStore(), SupabaseAppStore()
    cfg = replace(ScenarioConfig.from_row(store.config()), price_drift_prob=0.0, forced_book_outcomes=forced)
    llm = ScriptedLLM(list(llm_steps))
    return Orchestrator(app, store, FixedClock(NOW), llm, tools_config=cfg, sleep=lambda s: None), app, store, llm


@pytest.fixture
def user():
    uid = f"t_{uuid.uuid4().hex[:10]}"
    yield uid
    db = get_db()  # restore the provider world, then the app rows
    sids = [r["id"] for r in db.table("sessions").select("id").eq("user_id", uid).execute().data]
    aids = [r["id"] for r in db.table("approvals").select("id").in_("session_id", sids).execute().data] if sids else []
    holds, slots = set(), set()
    for r in (db.table("approvals").select("payload").in_("id", aids).execute().data if aids else []):
        p = r["payload"]
        holds.add(p.get("hold_id")), slots.update([p.get("slot_id"), p.get("to_slot_id")])
    keys = [f"idem_{a}" for a in aids] + [f"attempt:book:{h}" for h in holds if h] + \
           [f"attempt:cancel:idem_{a}" for a in aids]
    bookings = db.table("bookings").select("id,slot_id").eq("user_id", uid).execute().data
    slots.update(b["slot_id"] for b in bookings)
    if keys:
        db.table("idempotency_keys").delete().in_("key", keys).execute()
    db.table("bookings").delete().eq("user_id", uid).execute()
    if holds - {None}:
        db.table("holds").delete().in_("id", list(holds - {None})).execute()
    if slots - {None}:
        db.table("slots").update({"status": "free"}).in_("id", list(slots - {None})).execute()
    cleanup_user_data(db, [uid])


def to_card(o, app, uid):
    r1 = o.handle_message(uid, FIRST_MSG)
    o.llm.steps.append(lambda _: _propose(app, r1.session_id))
    r2 = o.handle_message(uid, "book the top one", r1.session_id)
    assert r2.approval_card, r2.reply
    return r1, r2


def _propose(app, sid):
    s = app.session(sid)["request_frame"]["_search"]
    pid = list(s["slots"])[0]
    return call("propose_book", provider_id=pid, slot_id=s["slots"][pid][0])


def my_bookings(uid):
    return get_db().table("bookings").select("*").eq("user_id", uid).execute().data


def test_full_flow_persists_everything_and_survives_timeout_after_success(user):
    o, app, store, llm = process(find_steps(), forced=("timeout_success",))
    r1, r2 = to_card(o, app, user)
    out = o.approve(user, r2.approval_card["approval_id"])
    assert out.reply.startswith("Booked and verified") and out.state == "DONE"

    b = my_bookings(user)
    assert len(b) == 1 and b[0]["status"] == "confirmed" and b[0]["idempotency_key"].startswith("idem_")
    # a brand-new process sees the same truth
    _, app2, _, _ = process()
    att = app2.unresolved_attempts(user)
    assert att == []
    sid = r1.session_id
    assert app2.session(sid)["state"] == "DONE" and app2.session(sid)["pending_approval_id"] is None
    assert [m["role"] for m in app2.messages(sid)][:2] == ["user", "assistant"]
    db = get_db()
    attempt = db.table("booking_attempts").select("*").execute().data
    mine = [a for a in attempt if a["approval_id"] == r2.approval_card["approval_id"]]
    assert len(mine) == 1 and mine[0]["status"] == "succeeded"
    assert db.table("approvals").select("status").eq("id", r2.approval_card["approval_id"]).execute().data[0]["status"] == "consumed"
    # the trace was persisted: model calls with tokens, the timeout, the verifying status call
    run1 = app2.spans(r1.run_id)
    assert any(s["kind"] == "model_call" and s["tokens_in"] > 0 for s in run1)
    ex = app2.spans(out.run_id)
    assert [s["name"] for s in ex if s["kind"] == "tool_call"] == ["exec.book", "exec.get_booking_status", "exec.get_booking_status"]
    assert ex[0]["kind"] == "tool_call" or any(s["error"] == "ToolTimeout" for s in ex)
    assert app2.run(out.run_id)["status"] == "done"


def test_real_restart_mid_booking_resumes_once(user):
    o1, app1, _, _ = process(find_steps())
    _, r2 = to_card(o1, app1, user)
    aid = r2.approval_card["approval_id"]
    a = approvals.approve(o1._deps(user, NullTracer()), aid, user)["approval"]
    att = app1.begin_attempt(a["id"], a["payload_hash"], a["payload"]["hold_id"], NOW)  # process 1 dies here
    assert my_bookings(user) == [] and att["status"] == "started"

    o2, app2, _, _ = process()  # process 2 boots
    notes = o2.recover()
    assert len(notes) == 1 and notes[0].startswith("Booked and verified")
    assert len(my_bookings(user)) == 1
    assert app2.attempt(att["id"])["status"] == "succeeded"
    o3, *_ = process()  # a third boot finds nothing left to do
    assert o3.recover() == [] and len(my_bookings(user)) == 1


def test_concurrent_double_approve_across_processes_books_once(user):
    o1, app1, _, _ = process(find_steps())
    _, r2 = to_card(o1, app1, user)
    aid = r2.approval_card["approval_id"]
    o_a, o_b = process()[0], process()[0]  # two servers, same database
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda o: o.approve(user, aid), [o_a, o_b]))
    replies = [r.reply for r in results]
    assert sum(r.startswith("Booked and verified") for r in replies) == 1, replies
    assert len(my_bookings(user)) == 1
    assert all(r.startswith("Booked and verified") or r.startswith("I'm still working") or "already used" in r
               for r in replies)


def test_lock_blocks_a_second_chat_for_the_same_user(user):
    o, app, _, _ = process([lambda _: None])
    app.acquire_lock(user, NOW, 60)
    r = o.handle_message(user, "hello")
    assert r.busy and r.reply.startswith("I'm still working")
