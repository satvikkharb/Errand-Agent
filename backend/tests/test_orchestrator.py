import json
from dataclasses import replace
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent import approvals
from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.executor import MSG_UNKNOWN
from app.agent.orchestrator import MSG_ERROR, MSG_LIMIT, Orchestrator
from app.agent.seed import SEED_SESSION_ID, seed_demo
from app.agent.tokens import messages_tokens
from app.agent.trace import NullTracer
from app.clock import IST, FixedClock
from app.llm import ScriptedLLM, call, say
from app.tools.config import ScenarioConfig
from app.tools.store import InMemoryStore

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
FIRST_MSG = "Find a plumber in Indiranagar tomorrow between 9 and 1, under 1500"


def find_steps():
    return [call("update_request_frame", service="plumber", area="Indiranagar", date_start="2026-11-03",
                 window_start="09:00", window_end="13:00", budget_max=1500),
            call("search_providers"), say("Top pick is QuickFix, rated 4.7 at ₹1200.")]


class H:
    def __init__(self, steps=(), *, seed=False, budget=6000, user_id="user_demo", max_turns=8, max_tools=12, **cfg):
        self.clock, self.store, self.app = FixedClock(NOW), InMemoryStore(), InMemoryAppStore()
        if seed:
            seed_demo(self.app, NOW)
        self.llm = ScriptedLLM(list(steps))
        base = ScenarioConfig.from_row(self.store.config())
        self.cfg = replace(base, **{"price_drift_prob": 0.0, **cfg})
        self.user_id, self.sid = user_id, None
        self.o = Orchestrator(self.app, self.store, self.clock, self.llm, builder=ContextBuilder(budget),
                              max_turns=max_turns, max_tool_calls=max_tools, tools_config=self.cfg,
                              sleep=lambda s: None)

    def chat(self, msg, *steps, session_id=None):
        self.llm.steps.extend(steps)
        sid = session_id or self.sid or self.app.create_session(self.user_id)["id"]  # steps may read the session
        self.sid = self.sid or sid
        return self.o.handle_message(self.user_id, msg, sid)

    def propose(self, idx=0):
        def step(_):
            s = self.app.session(self.sid)["request_frame"]["_search"]
            pid = list(s["slots"])[idx]
            return call("propose_book", provider_id=pid, slot_id=s["slots"][pid][0])
        return step

    def to_card(self, idx=0):
        self.chat(FIRST_MSG, *find_steps())
        r = self.chat("book it for me", self.propose(idx))
        assert r.approval_card, r.reply
        return r.approval_card["approval_id"]

    def bookings(self):
        return self.store.bookings_for_user(self.user_id)

    def attempts(self):
        return list(self.app._attempts.values())

    def spans(self, run_id, kind=None, name=None):
        return [s for s in self.app.spans(run_id)
                if (kind is None or s["kind"] == kind) and (name is None or s["name"] == name)]


# ================================================================ happy path
def test_full_booking_flow_with_trace():
    h = H(success := [], forced_book_outcomes=("success",))
    r1 = h.chat(FIRST_MSG, *find_steps())
    assert r1.state == "PICK" and r1.approval_card is None and "QuickFix" in r1.reply
    r2 = h.chat("book it for me", h.propose())
    assert r2.state == "AWAIT_APPROVAL" and r2.approval_card["buttons"] == ["Approve", "Cancel"]
    before = len(h.bookings())
    r3 = h.o.approve("user_demo", r2.approval_card["approval_id"])
    assert r3.reply.startswith("Booked and verified") and r3.state == "DONE" and r3.approval_card is None
    assert len(h.bookings()) == before + 1
    att = h.attempts()[0]
    assert att["status"] == "succeeded" and att["idempotency_key"].startswith("idem_")
    # trace of the first run: context + model_call (tokens, cost) + tool calls + state transitions
    run1 = r1.run_id
    assert h.spans(run1, "context") and h.spans(run1, "state_transition")
    mc = h.spans(run1, "model_call")[0]
    assert mc["tokens_in"] > 0 and mc["cost_usd"] is not None
    assert [s["name"] for s in h.spans(run1, "tool_call")] == ["update_request_frame", "search_providers"]
    run = h.app.run(run1)
    assert run["status"] == "done" and run["turns"] == 3 and run["tool_calls"] == 2
    # execution trace
    names = [s["name"] for s in h.spans(r3.run_id, "tool_call")]
    assert names == ["exec.book", "exec.get_booking_status"]


# ================================================================ booking correctness
def test_timeout_after_success_is_verified_not_duplicated():
    h = H(forced_book_outcomes=("timeout_success",))
    aid = h.to_card()
    n = len(h.bookings())
    r = h.o.approve("user_demo", aid)
    assert r.reply.startswith("Booked and verified")
    assert len(h.bookings()) == n + 1
    names = [s["name"] for s in h.spans(r.run_id, "tool_call")]
    assert names == ["exec.book", "exec.get_booking_status", "exec.get_booking_status"]
    assert h.spans(r.run_id, "tool_call")[0]["error"] == "ToolTimeout"
    assert h.attempts()[0]["status"] == "succeeded"


def test_timeout_without_booking_retries_with_same_key():
    h = H(forced_book_outcomes=("timeout_fail", "success"))
    aid = h.to_card()
    n = len(h.bookings())
    r = h.o.approve("user_demo", aid)
    assert r.reply.startswith("Booked and verified") and len(h.bookings()) == n + 1
    book_calls = h.spans(r.run_id, "tool_call", "exec.book")
    assert len(book_calls) == 2 and len({c["input"]["args"][1] for c in book_calls}) == 1
    assert h.attempts()[0]["retry_count"] >= 1


def test_unresolved_outcome_never_claims_success_then_reconciles_later():
    h = H(forced_book_outcomes=("timeout_fail",))
    aid = h.to_card()
    n = len(h.bookings())
    r = h.o.approve("user_demo", aid)
    assert r.reply == MSG_UNKNOWN and "Booked" not in r.reply and r.state == "RECONCILE"
    assert len(h.bookings()) == n and h.attempts()[0]["status"] == "unknown"
    h.clock.advance(minutes=11)  # the hold runs out; next interaction reconciles first
    r2 = h.chat("hello", say("Hi!"))
    assert any("ran out" in x for x in r2.notices) and h.attempts()[0]["status"] == "failed"
    assert len(h.bookings()) == n
    assert h.app.notifications("user_demo")


def test_slot_taken_and_hold_expired_leave_no_booking():
    h = H(forced_book_outcomes=("slot_taken",))
    n = len(h.bookings())
    r = h.o.approve("user_demo", h.to_card())
    assert "just taken" in r.reply and r.state == "SEARCH" and len(h.bookings()) == n

    h2 = H(forced_book_outcomes=("success",))
    aid = h2.to_card()
    h2.clock.advance(minutes=11)
    r2 = h2.o.approve("user_demo", aid)
    assert "expired" in r2.reply and len(h2.bookings()) == 5 and r2.state == "SEARCH"


def test_repeated_confirmation_books_once():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    first = h.o.approve("user_demo", aid)
    second = h.o.approve("user_demo", aid)
    assert first.reply.startswith("Booked") and "already used" in second.reply
    assert len(h.bookings()) == 6 and len(h.attempts()) == 1
    r = h.chat("yes", say("Anything else?"))  # nothing pending: a bare 'yes' is just chat
    assert len(h.bookings()) == 6 and r.reply == "Anything else?"


def test_typed_yes_approves_only_when_exact():
    h = H(forced_book_outcomes=("success",))
    h.to_card()
    r = h.chat("yes but cheaper please", say("Sure, let me look."))
    assert len(h.bookings()) == 5 and r.approval_card  # still pending
    r2 = h.chat("Yes!")
    assert r2.reply.startswith("Booked and verified") and len(h.bookings()) == 6


def test_typed_no_declines_and_frees_the_slot():
    h = H()
    aid = h.to_card()
    slot = h.app.approval(aid)["payload"]["slot_id"]
    r = h.chat("no")
    assert "won't do that" in r.reply and r.approval_card is None and r.state == "SEARCH"
    assert h.store.slot(slot)["status"] == "free"


def test_restart_mid_booking_is_resumed_once():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    deps = h.o._deps("user_demo", NullTracer())
    a = approvals.approve(deps, aid, "user_demo")["approval"]
    h.app.begin_attempt(a["id"], a["payload_hash"], a["payload"]["hold_id"], NOW)  # crash right after this
    assert len(h.bookings()) == 5 and h.attempts()[0]["status"] == "started"
    fresh = Orchestrator(h.app, h.store, h.clock, ScriptedLLM([]), tools_config=h.cfg, sleep=lambda s: None)
    notes = fresh.recover()
    assert len(notes) == 1 and notes[0].startswith("Booked and verified")
    assert len(h.bookings()) == 6 and h.attempts()[0]["status"] == "succeeded"
    assert fresh.recover() == [] and len(h.bookings()) == 6  # second restart does nothing


def test_restart_after_hold_expired_does_not_book():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    a = approvals.approve(h.o._deps("user_demo", NullTracer()), aid, "user_demo")["approval"]
    h.app.begin_attempt(a["id"], a["payload_hash"], a["payload"]["hold_id"], NOW)
    h.clock.advance(minutes=30)
    notes = h.o.recover()
    assert "ran out" in notes[0] and len(h.bookings()) == 5 and h.attempts()[0]["status"] == "failed"


# ================================================================ cancel / reschedule
def test_cancel_retries_503_with_same_key_and_verifies():
    h = H()
    r = h.chat("cancel my plumber booking", call("list_bookings"), call("propose_cancel", booking_id="bk_005"))
    assert r.approval_card["title"] == "Confirm cancellation"
    out = h.o.approve("user_demo", r.approval_card["approval_id"])
    assert out.reply.startswith("Cancelled and verified")
    assert h.store.booking("bk_005")["status"] == "cancelled"
    calls = h.spans(out.run_id, "tool_call", "exec.cancel")
    assert [c["error"] for c in calls] == ["ToolUnavailable", None]
    assert len({c["input"]["args"][1] for c in calls}) == 1


def test_reschedule_conflict_keeps_original_booking():
    h = H()
    h.store.set_slot_status("ac_01_1107_09", "free")  # make the configured-conflict slot offerable
    r = h.chat("move my AC servicing to Saturday",
               call("get_availability", provider_id="ac_01", date_start="2026-11-07", date_end="2026-11-07"),
               call("propose_reschedule", booking_id="bk_004", slot_id="ac_01_1107_09"))
    out = h.o.approve("user_demo", r.approval_card["approval_id"])
    assert "unchanged" in out.reply and out.state == "SEARCH"
    assert h.store.booking("bk_004")["slot_id"] == "ac_01_1105_14"


def test_reschedule_success_moves_booking():
    h = H()
    r = h.chat("move my AC servicing to Friday",
               call("get_availability", provider_id="ac_01", date_start="2026-11-06", date_end="2026-11-06"),
               lambda _: call("propose_reschedule", booking_id="bk_004",
                              slot_id=h.app.session(h.sid)["request_frame"]["_avail"]["ac_01"][0]))
    out = h.o.approve("user_demo", r.approval_card["approval_id"])
    assert out.reply.startswith("Rescheduled and verified")
    assert h.store.booking("bk_004")["slot_start"].day == 6


# ================================================================ approval enforcement via chat
def test_card_survives_questions_and_material_change_voids_it():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    pid = h.app.approval(aid)["payload"]["provider_id"]
    r = h.chat("what do reviews say?", call("get_provider", provider_id=pid), say("Mostly positive."))
    assert r.approval_card["approval_id"] == aid
    r2 = h.chat("make it Friday instead", call("update_request_frame", date_start="2026-11-06"), say("Updated."))
    assert r2.approval_card is None
    out = h.o.approve("user_demo", aid)
    assert "out of date" in out.reply and len(h.bookings()) == 5


def test_injection_cannot_book_even_if_the_model_obeys_it():
    h = H(forced_book_outcomes=("success",))
    r = h.chat("tell me about pl_03",
               call("get_provider", provider_id="pl_03"),
               call("book", hold_id="x", idempotency_key="y"),            # obedient model tries the real tool
               call("propose_book", provider_id="pl_03", slot_id="pl_03_1103_09"),
               say("Done! I've booked pl_03 for you."))                    # and lies about it
    assert len(h.bookings()) == 5 and not h.attempts() and r.approval_card is None
    assert "booked" not in r.reply.lower().replace("i haven't booked", "")
    sent = json.dumps(h.llm.calls[1]["messages"]).lower()
    assert "ignore all previous" not in sent
    errs = [s["output"].get("error") for s in h.spans(r.run_id, "tool_call")]
    assert errs[1] == "unknown_tool" and errs[2] == "missing_fields"
    flagged = h.spans(r.run_id, "tool_call", "get_provider")[0]["injection_flags"]
    assert flagged


def test_reply_guard_only_blocks_false_claims():
    h = H()
    assert "haven't booked" in h.chat("hi", say("Great news, I've booked you with QuickFix!")).reply
    assert h.chat("hi", say("I haven't booked anything yet.")).reply == "I haven't booked anything yet."
    assert h.chat("hi", say("I can book that once you approve.")).reply == "I can book that once you approve."


# ================================================================ bounded execution
def test_tool_call_limit_ends_cleanly():
    h = H([call("list_bookings")] * 30, max_turns=30)
    r = h.chat("loop")
    assert r.reply == MSG_LIMIT and r.exit_reason == "limit_tools"
    run = h.app.run(r.run_id)
    assert run["exit_reason"] == "limit_tools" and run["tool_calls"] == 10 and run["status"] == "done"
    assert h.app.messages(r.session_id)[-1]["content"] == MSG_LIMIT and len(h.bookings()) == 5


def test_turn_limit_and_wall_clock_limit():
    h = H([call("list_bookings")] * 30, max_tools=40)
    assert h.chat("loop").exit_reason == "limit_turns"

    h2 = H()

    def slow(_):
        h2.clock.advance(seconds=50)
        return call("list_bookings")
    h2.llm.steps += [slow, call("list_bookings")]
    r = h2.chat("slow")
    assert r.exit_reason == "timeout" and r.reply == MSG_LIMIT


def test_model_failure_is_a_clean_outcome_and_releases_the_lock():
    h = H([RuntimeError("openai exploded")])
    r = h.chat("hello")
    assert r.reply == MSG_ERROR and r.exit_reason == "error" and h.app.run(r.run_id)["status"] == "failed"
    assert h.chat("hello again", say("Hi!")).reply == "Hi!"


def test_busy_lock_blocks_chat_and_approve():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    h.app.acquire_lock("user_demo", NOW)
    assert h.o.handle_message("user_demo", "hi", h.sid).busy
    assert h.o.approve("user_demo", aid).busy and len(h.bookings()) == 5
    h.app.release_lock("user_demo")
    assert h.o.approve("user_demo", aid).reply.startswith("Booked")


def test_input_and_ownership_validation():
    h = H()
    assert h.o.handle_message("user_demo", "   ").exit_reason == "rejected_input"
    assert h.o.handle_message("user_demo", "x" * 2001).exit_reason == "rejected_input"
    assert h.o.handle_message("user_other", "hi", "00000000-dead").exit_reason == "not_found"
    aid = H(forced_book_outcomes=("success",)).to_card  # noqa: F841 (separate world)
    h2 = H()
    aid2 = h2.to_card()
    assert h2.o.approve("user_other", aid2).exit_reason == "not_found"


# ================================================================ context management
def test_context_is_capped_filtered_and_logged():
    tools_tokens = Orchestrator(InMemoryAppStore(), InMemoryStore(), FixedClock(NOW), ScriptedLLM([])).tools_tokens
    budget = tools_tokens + 1300
    h = H([say("Sure.")], seed=True, budget=budget)
    r = h.chat("any plumber update?", session_id=SEED_SESSION_ID)
    sent = h.llm.calls[0]["messages"]
    text = "\n".join(m["content"] for m in sent)
    assert messages_tokens(sent) + tools_tokens <= budget
    ctx = h.spans(r.run_id, "context")[0]["output"]
    assert ctx["tokens"] <= budget and ctx["budget"] == budget
    # only relevant, confirmed, current facts
    assert "preferred_area: Indiranagar" in text
    for never in ("prefers_cheapest", "likes_high_rating", "prefers_weekdays", "preferred_time_of_day",
                  "old_area", "gate_code", "phone_note"):
        assert never not in text
    assert text.count("Low-relevance preference") <= 2 and ctx["memories_dropped"] >= 40
    assert "preferred_electrician" not in text  # other service's key is not relevant here either
    # recent turns kept, oldest dropped, summary present
    assert "Earlier assistant turn 60" in text and "Earlier user turn 1:" not in text
    assert ctx["history_dropped"] > 0 and "Amit Electricals" in text
    # the log names exactly what was sent
    mem_ids = {m["id"] for m in h.app.active_memories("user_demo")}
    assert set(ctx["memory_ids"]) <= mem_ids and len(ctx["memory_ids"]) <= 8


def test_forgotten_fact_never_reaches_the_model_again():
    h = H([call("forget_memory", key="preferred_area", quote="forget my area"), say("Done, I've forgotten it.")],
          seed=True)
    h.chat("please forget my area", session_id=SEED_SESSION_ID)
    assert "Indiranagar" in "\n".join(m["content"] for m in h.llm.calls[0]["messages"])  # sanity: it was visible
    h.chat("hello", say("Hi!"))
    after = "\n".join(m["content"] for m in h.llm.calls[-1]["messages"])
    assert "Indiranagar" not in after and "preferred_area" not in after
    assert h.app.valid_summaries("user_demo") == []


def test_tool_results_are_dropped_before_budget_is_blown():
    tools_tokens = Orchestrator(InMemoryAppStore(), InMemoryStore(), FixedClock(NOW), ScriptedLLM([])).tools_tokens
    h = H([call("list_bookings"), call("list_bookings"), call("list_bookings"), say("ok")], budget=tools_tokens + 700)
    r = h.chat("list my bookings a few times")
    ctxs = [s["output"] for s in h.spans(r.run_id, "context")]
    assert all(c["tokens"] <= c["budget"] for c in ctxs) and ctxs[-1]["tool_results_stubbed"] >= 1


# ================================================================ API
def test_api_chat_approve_and_trace(monkeypatch):
    h = H(forced_book_outcomes=("success",))
    h.llm.steps.extend(find_steps())
    import app.main as main
    monkeypatch.setattr(main, "get_orchestrator", lambda: h.o)
    c = TestClient(main.app)
    r1 = c.post("/chat", json={"user_id": "user_demo", "message": FIRST_MSG}).json()
    h.sid = r1["session_id"]
    h.llm.steps.append(h.propose())
    r2 = c.post("/chat", json={"user_id": "user_demo", "message": "book it", "session_id": h.sid}).json()
    aid = r2["approval_card"]["approval_id"]
    done = c.post(f"/approvals/{aid}/approve", json={"user_id": "user_demo"}).json()
    assert done["reply"].startswith("Booked and verified")
    tr = c.get(f"/runs/{r1['run_id']}/trace").json()
    assert tr["run"]["status"] == "done" and any(s["kind"] == "model_call" for s in tr["spans"])
    assert c.get(f"/sessions/{h.sid}/messages", params={"user_id": "user_other"}).status_code == 404
    assert c.post("/chat", json={"user_id": "", "message": "x"}).status_code == 422


def test_api_503_without_openai_key(monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "get_orchestrator", lambda: None)
    assert TestClient(main.app).post("/chat", json={"user_id": "u", "message": "hi"}).status_code == 503
