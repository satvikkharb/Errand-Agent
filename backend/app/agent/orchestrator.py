"""The run loop. One entry point per user action: a chat message, or an Approve/Reject tap.

Every path ends in a user-facing message: normal reply, limit hit, model failure, busy, or a
reconcile notice. Side effects only ever happen in Executor, after a valid approval.
"""
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta

from app.agent import approvals, states
from app.agent.budget import BudgetExceeded, RunBudget
from app.agent.context import ContextBuilder
from app.agent.deps import Deps
from app.agent.executor import Executor
from app.agent.gateway import GatewayContext, ToolGateway, tool_specs
from app.agent.memory import MemoryService
from app.agent.options import build_options
from app.agent.proactive import ProactiveProcessor
from app.agent.sanitize import clean_text
from app.agent.summarizer import Summarizer
from app.agent.timefmt import when
from app.agent.titles import HistoryService, TitleService
from app.agent.tokens import count_tokens
from app.agent.trace import AppTracer
from app.llm import LLM
from app.tools.provider_tools import ProviderTools

log = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 2000
LOCK_TTL_S = 60

MSG_LIMIT = ("I'm stopping here because this is taking too long. Nothing was booked, cancelled or changed, "
             "and your details are saved. Tell me how you'd like to continue.")
MSG_ERROR = ("Sorry, something went wrong on my side and I couldn't finish. Nothing was booked, cancelled or "
             "changed. Please try again.")
MSG_BUSY = "I'm still working on your previous request. Please try again in a moment."
MSG_EMPTY = "Sorry, I didn't catch that. Could you say it again?"
MSG_NO_CLAIM = ("I haven't booked, cancelled or changed anything yet. {tail}")
PROPOSAL_TEXT = {"book": "Here's what I'd book. Please review it and tap Approve to confirm.",
                 "cancel": "Here's what I'd cancel. Please review it and tap Approve to confirm.",
                 "reschedule": "Here's the change I'd make. Please review it and tap Approve to confirm."}
APPROVAL_ERRORS = {
    "approval_expired": "That approval expired, so nothing was done. Want me to look again?",
    "hold_expired": "The hold on that slot ran out, so nothing was booked. Want me to search for slots again?",
    "approval_consumed": "That approval was already used, so I haven't done anything new.",
    "approval_approved": "That approval was already used, so I haven't done anything new.",
    "approval_invalidated": "That card is out of date because something changed. Please use the latest one.",
    "approval_rejected": "You already declined that one.",
    "approval_tampered": "That approval failed an integrity check, so I did nothing.",
    "not_found": "I can't find that approval.",
}
AFFIRM = {"yes", "y", "yep", "approve", "approved", "confirm", "confirmed", "book it", "yes book it",
          "yes please", "go ahead", "do it"}
REJECT = {"no", "n", "nope", "reject", "dont", "do not"}
MEMORY_YES = {"yes", "y", "yep", "yeah", "sure", "ok", "okay", "yes please", "remember it", "remember that", "please do"}
MEMORY_NO = {"no", "n", "nope", "no thanks", "no thank you", "dont", "do not", "dont remember", "not really"}
_CLAIM = re.compile(r"\b(i('| ha)ve|has been|have been|is now|are now|you('re| are)|successfully|all set)\b.{0,40}"
                    r"\b(booked|confirmed|cancel{1,2}ed|rescheduled|moved)\b|\b(booked|confirmed) (it|that|your)\b", re.I)
_HEDGE = re.compile(r"\b(not|n't|never|nothing|yet|until|won't|if|once)\b", re.I)


@dataclass
class ChatResult:
    session_id: str | None
    reply: str
    state: str | None
    run_id: str | None = None
    exit_reason: str | None = None
    approval_card: dict | None = None
    user_views: list = field(default_factory=list)
    notices: list = field(default_factory=list)
    busy: bool = False
    memory_prompt: dict | None = None  # "Want me to remember X?" with Yes/No buttons
    title: str | None = None  # the conversation's current title, so the sidebar can update at once
    options: dict | None = None  # the provider table (every match with ALL its slots) when a search just ran


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", s.lower()).split())


def guard_reply(text: str, has_pending: bool) -> str:
    """The model can't book, so it must not claim it did. Replace such claims with the truth."""
    if _CLAIM.search(text) and not _HEDGE.search(text):
        return MSG_NO_CLAIM.format(tail="Tap Approve on the card when you're ready." if has_pending
                                   else "I can set it up for your approval if you'd like.")
    return text


class AppTracerNull:
    def span(self, *a, **k):
        pass


class Orchestrator:
    def __init__(self, app, store, clock, llm: LLM, *, builder: ContextBuilder | None = None, max_turns: int = 8,
                 max_tool_calls: int = 12, timeout_s: int = 45, sleep=time.sleep, tools_config=None,
                 allow_typed_approval: bool = True, price_in_per_1m: float = 0.40, price_out_per_1m: float = 1.60,
                 summary_trigger: int = 30, summary_keep: int = 12, llm_titles: bool = False):
        self.app, self.store, self.clock, self.llm = app, store, clock, llm
        self.builder = builder or ContextBuilder()
        self.max_turns, self.max_tool_calls, self.timeout_s = max_turns, max_tool_calls, timeout_s
        self.sleep, self.tools_config, self.allow_typed = sleep, tools_config, allow_typed_approval
        self.price_in, self.price_out = price_in_per_1m, price_out_per_1m
        self.specs = tool_specs()
        self.proactive = ProactiveProcessor(app, store, clock, self._deps)
        self.memory = MemoryService(app, clock)
        self.summarizer = Summarizer(app, llm, clock, summary_trigger, summary_keep)
        self.titles = TitleService(app, llm, clock, use_llm=llm_titles)
        self.history = HistoryService(app, clock)
        self.tools_tokens = count_tokens(json.dumps(self.specs))

    # ------------------------------------------------------------------ wiring
    def _deps(self, user_id: str, tracer) -> Deps:
        tools = ProviderTools(self.store, self.clock, self.tools_config, user_id=user_id)
        return Deps(self.app, self.store, tools, self.clock, tracer)

    def _open_run(self, session_id: str):
        run = self.app.create_run(session_id, "user_message",
                                  self.clock.now() + timedelta(seconds=self.timeout_s), self.clock.now())
        return run, AppTracer(self.app, run["id"])

    def _close_run(self, run, session_id, reply, exit_reason, budget=None, tracer=None):
        self.app.add_message(session_id, "assistant", reply, run["id"])
        if tracer is not None:  # first exchange done: let the model write a nicer title (before spans are saved)
            self.titles.upgrade(session_id, tracer)
        self.app.update_run(run["id"], status="failed" if exit_reason == "error" else "done",
                            exit_reason=exit_reason, final_message=reply, ended_at=self.clock.now(),
                            turns=budget.turns if budget else 0, tool_calls=budget.tool_calls if budget else 0)

    def _card(self, session_id) -> dict | None:
        s = self.app.session(session_id)
        a = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
        return approvals.render_card(a) if a and a["status"] == "pending" else None

    def _result(self, session_id, reply, run, exit_reason, views=(), notices=(), memory_prompt=None, options=None):
        s = self.app.session(session_id)
        return ChatResult(session_id, reply, s["state"], run["id"], exit_reason, self._card(session_id),
                          list(views), list(notices), memory_prompt=memory_prompt, title=s.get("title"), options=options)

    def _options(self, session_id: str) -> dict | None:
        s = self.app.session(session_id)
        pending = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
        return build_options(s["request_frame"], pending)

    def _provider_name(self, pid: str) -> str | None:
        p = self.store.provider(pid)
        return clean_text(p["name"], 60)[0] if p else None

    # ------------------------------------------------------------ chat message
    def handle_message(self, user_id: str, message: str, session_id: str | None = None) -> ChatResult:
        message = (message or "").strip()
        if not message or len(message) > MAX_MESSAGE_CHARS:
            return ChatResult(session_id, MSG_EMPTY if not message else
                              f"That message is too long. Please keep it under {MAX_MESSAGE_CHARS} characters.",
                              None, exit_reason="rejected_input")
        if session_id:
            s = self.app.session(session_id)
            if not s or s["user_id"] != user_id or s.get("deleted_at"):
                return ChatResult(None, "I can't find that conversation.", None, exit_reason="not_found")
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return ChatResult(session_id, MSG_BUSY, None, exit_reason="busy", busy=True)
        try:
            return self._handle(user_id, message, session_id)
        finally:
            self.app.release_lock(user_id)

    def _handle(self, user_id, message, session_id) -> ChatResult:
        s = self.app.session(session_id) if session_id else self.app.create_session(user_id)
        sid = s["id"]
        run, tracer = self._open_run(sid)
        deps = self._deps(user_id, tracer)
        notices = self._reconcile(user_id, deps) + self._proactive_for_user(user_id)
        msg_row = self.app.add_message(sid, "user", message, run["id"])
        self.titles.ensure_initial(sid, message)  # the sidebar has a title from the first real message
        budget = RunBudget(self.clock, self.max_turns, self.max_tool_calls, timeout_s=self.timeout_s)
        views: list = []
        prompt = None
        searched_before = (self.app.session(sid)["request_frame"].get("_search") or {}).get("search_id")
        try:
            typed = self._typed_decision(deps, sid, user_id, message)
            if typed is not None:
                (reply, prompt), exit_reason = typed, "done"
            else:
                self.summarizer.ensure(sid, user_id, tracer)  # keeps a long chat inside the context budget
                reply, exit_reason = self._agent_loop(deps, user_id, sid, msg_row, budget, tracer, views)
        except BudgetExceeded as e:
            reply, exit_reason = MSG_LIMIT, e.reason
            tracer.span("state_transition", "run_limit", error=e.reason)
        except Exception:
            log.exception("run failed")
            reply, exit_reason = MSG_ERROR, "error"
        if exit_reason == "done" and not views:
            reply = guard_reply(reply, self._card(sid) is not None)
        if prompt:
            reply = f"{reply}\n\n{prompt['text']}"
        self._close_run(run, sid, reply, exit_reason, budget, tracer)
        searched_now = (self.app.session(sid)["request_frame"].get("_search") or {}).get("search_id")
        options = self._options(sid) if searched_now and searched_now != searched_before else None  # a NEW search this turn
        return self._result(sid, reply, run, exit_reason, views, notices, prompt, options)

    def _typed_decision(self, deps: Deps, sid: str, user_id: str, message: str) -> tuple[str, dict | None] | None:
        """A bare yes/no answers the ONE open question: a pending approval card, else a 'remember this?' prompt.
        Anything else goes to the model."""
        s = self.app.session(sid)
        open_q = s["request_frame"].get("_confirm_memory")
        if open_q:  # a memory question lives for ONE message, so a later stray "yes" can never confirm it
            frame = s["request_frame"]
            frame.pop("_confirm_memory")
            self.app.update_session(sid, request_frame=frame)
        n = _norm(message)
        if self.allow_typed and s["pending_approval_id"]:
            if n in AFFIRM:
                return self._approve_inner(deps, user_id, s["pending_approval_id"])
            if n in REJECT:
                res = approvals.reject(deps, s["pending_approval_id"], user_id)
                if res["ok"]:
                    self._move_quiet(deps, sid, "SEARCH")
                    return "Okay, I won't do that. Tell me what you'd like instead.", None
            return None
        if self.allow_typed and open_q:
            if n in MEMORY_YES:
                m = self.memory.confirm(user_id, open_q)
                if m:
                    return f"Got it, I'll remember {self._describe(m)}.", None
            elif n in MEMORY_NO and self.memory.reject(user_id, open_q):
                return "Okay, I won't remember that.", None
        return None

    def _describe(self, m: dict) -> str:
        if m["key"] == "preferred_time_of_day":
            return f"that you prefer {m['value']}s"
        label = m["key"].removeprefix("preferred_").replace("_", " ")
        return f"{self._provider_name(m['value']) or m['value']} as your preferred {label} provider"

    # ---------------------------------------------------------------- agent loop
    def _agent_loop(self, deps, user_id, sid, msg_row, budget, tracer, views) -> tuple[str, str]:
        gw = ToolGateway(deps, GatewayContext(sid, user_id, msg_row["content"], msg_row["id"]), budget)
        run_msgs: list[dict] = []
        while True:
            budget.charge_turn()
            s = self.app.session(sid)
            pending = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
            ctx = self.builder.build(
                now=self.clock.now(), session=s, memories=self.app.active_memories(user_id),
                summaries=self.app.valid_summaries(user_id), history=self.app.messages(sid),
                run_messages=run_msgs, tools_tokens=self.tools_tokens, pending_approval=pending)
            tracer.span("context", "context", output=ctx.report)
            t0 = time.perf_counter()
            resp = self.llm.complete(ctx.messages, self.specs)
            cost = (resp.usage.prompt_tokens * self.price_in + resp.usage.completion_tokens * self.price_out) / 1e6
            tracer.span("model_call", resp.model, input={"messages": len(ctx.messages), "tokens": ctx.report["tokens"]},
                        output={"content": (resp.content or "")[:300],
                                "tool_calls": [{"name": t.name, "arguments": t.arguments[:300]} for t in resp.tool_calls]},
                        latency_ms=int((time.perf_counter() - t0) * 1000), tokens_in=resp.usage.prompt_tokens,
                        tokens_out=resp.usage.completion_tokens, cost_usd=round(cost, 6), cost_estimated=True)
            if not resp.tool_calls:
                return (resp.content or "").strip() or MSG_EMPTY, "done"
            run_msgs.append({"role": "assistant", "content": resp.content or "", "tool_calls": [
                {"id": t.id, "type": "function", "function": {"name": t.name, "arguments": t.arguments}}
                for t in resp.tool_calls]})
            for t in resp.tool_calls:
                out = gw.call(t.name, t.arguments)
                run_msgs.append({"role": "tool", "tool_call_id": t.id, "content": json.dumps(out.model_view)})
                if out.user_view is not None:
                    views.append(out.user_view)
                if out.ends_turn:  # a proposal: stop and show the card. Skip any further calls.
                    action = (out.name or "propose_book").removeprefix("propose_")
                    return PROPOSAL_TEXT.get(action, PROPOSAL_TEXT["book"]), "done"

    # ---------------------------------------------------------------- approvals
    def approve(self, user_id: str, approval_id: str) -> ChatResult:
        a = self.app.approval(approval_id)
        s = self.app.session(a["session_id"]) if a else None
        if not a or s["user_id"] != user_id:
            return ChatResult(None, APPROVAL_ERRORS["not_found"], None, exit_reason="not_found")
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return ChatResult(s["id"], MSG_BUSY, s["state"], exit_reason="busy", busy=True)
        try:
            run, tracer = self._open_run(s["id"])
            deps = self._deps(user_id, tracer)
            notices = self._reconcile(user_id, deps)
            reply, prompt = self._approve_inner(deps, user_id, approval_id)
            if prompt:
                reply = f"{reply}\n\n{prompt['text']}"
            self._close_run(run, s["id"], reply, "done")
            return self._result(s["id"], reply, run, "done", notices=notices, memory_prompt=prompt)
        finally:
            self.app.release_lock(user_id)

    def _approve_inner(self, deps: Deps, user_id: str, approval_id: str) -> tuple[str, dict | None]:
        res = approvals.approve(deps, approval_id, user_id)
        if not res["ok"]:
            a = self.app.approval(approval_id)
            if res["error"] in ("approval_expired", "hold_expired") and a:  # clean up and unstick the session
                approvals.invalidate_pending(deps, a["session_id"], reason=res["error"])
                self._move_quiet(deps, a["session_id"], "SEARCH")
            return APPROVAL_ERRORS.get(res["error"], "I couldn't use that approval, so I did nothing."), None
        out = Executor(deps, sleep=self.sleep).run(res["approval"])
        prompt = None
        if out.outcome == "booked":  # a verified booking is the moment patterns can be noticed and asked about
            prompt = self.memory.after_booking(user_id, res["approval"]["session_id"],
                                               self.store.bookings_for_user(user_id), self._provider_name)
        return out.message, prompt

    def reject(self, user_id: str, approval_id: str) -> ChatResult:
        a = self.app.approval(approval_id)
        s = self.app.session(a["session_id"]) if a else None
        if not a or s["user_id"] != user_id:
            return ChatResult(None, APPROVAL_ERRORS["not_found"], None, exit_reason="not_found")
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return ChatResult(s["id"], MSG_BUSY, s["state"], exit_reason="busy", busy=True)
        try:
            run, tracer = self._open_run(s["id"])
            deps = self._deps(user_id, tracer)
            res = approvals.reject(deps, approval_id, user_id)
            if res["ok"]:
                self._move_quiet(deps, s["id"], "SEARCH")
            reply = "Okay, I won't do that. Tell me what you'd like instead." if res["ok"] else \
                APPROVAL_ERRORS.get(res["error"], "I couldn't decline that approval.")
            self._close_run(run, s["id"], reply, "done")
            return self._result(s["id"], reply, run, "done")
        finally:
            self.app.release_lock(user_id)

    # ----------------------------------------------------------------- recovery
    def _reconcile(self, user_id: str | None, deps: Deps, lock: bool = False) -> list[str]:
        """Resolve attempts left 'started'/'unknown' by a crash or an unclear outcome. Runs lazily on the next
        interaction (caller already holds the user lock) and from recover() (lock=True: take it per user, skip
        users who are mid-request). Returns notices to show the user."""
        notices = []
        for att in self.app.unresolved_attempts(user_id):
            a = self.app.approval(att["approval_id"])
            s = self.app.session(a["session_id"])
            if lock and not self.app.acquire_lock(s["user_id"], self.clock.now(), LOCK_TTL_S):
                continue
            try:
                d = self._deps(s["user_id"], deps.tracer)
                res = Executor(d, sleep=self.sleep).resume(a, att)
                if res.outcome != "unknown":
                    self.app.add_notification(s["user_id"], s["id"], res.message, self.clock.now())
                    notices.append(res.message)
            finally:
                if lock:
                    self.app.release_lock(s["user_id"])
        return notices

    def recover(self) -> list[str]:
        """Process-start hook: close runs a crash left open, finish every unresolved attempt, drain events."""
        self.app.close_stale_runs(self.clock.now())
        run_tracer = type("T", (), {"span": staticmethod(lambda *a, **k: None)})()
        notes = self._reconcile(None, Deps(self.app, self.store, None, self.clock, run_tracer), lock=True)
        self.tick()
        return notes

    # ---------------------------------------------------------------- proactive
    def tick(self, user_id: str | None = None, limit: int = 20) -> list[dict]:
        """Process pending world events (cron, app open, process start). A busy user's event waits for next tick."""
        self.store.requeue_stale_events(self.clock.now(), 300)
        results = []
        for ev in self.store.pending_events(user_id, limit):
            uid = ev["payload"].get("user_id")
            if not uid or not self.app.acquire_lock(uid, self.clock.now(), LOCK_TTL_S):
                continue
            try:
                if self.store.claim_event(ev["id"], self.clock.now()):  # only one worker wins the event
                    results.append(self.proactive.process_safe(ev))
            finally:
                self.app.release_lock(uid)
        return results

    def _proactive_for_user(self, user_id: str) -> list[str]:
        """Caller holds the user's lock: handle this user's pending events and return their notices."""
        out = []
        for ev in self.store.pending_events(user_id, 10):
            if self.store.claim_event(ev["id"], self.clock.now()):
                r = self.proactive.process_safe(ev)
                if r.get("body"):
                    out.append(r["body"])
        return out

    def open_proposal(self, user_id: str, session_id: str) -> ChatResult:
        """The user opened a replacement suggestion: create the fresh hold and approval card now."""
        s = self.app.session(session_id)
        if not s or s["user_id"] != user_id:
            return ChatResult(None, "I can't find that conversation.", None, exit_reason="not_found")
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return ChatResult(session_id, MSG_BUSY, s["state"], exit_reason="busy", busy=True)
        try:
            run, tracer = self._open_run(session_id)
            budget = RunBudget(self.clock, max_turns=1, max_tool_calls=self.max_tool_calls, timeout_s=self.timeout_s)
            try:
                reply, outcome = self.proactive.open_proposal(user_id, session_id, tracer, budget)
                exit_reason = "done"
            except BudgetExceeded as e:
                reply, exit_reason = MSG_LIMIT, e.reason
            self._close_run(run, session_id, reply, exit_reason, budget)
            return self._result(session_id, reply, run, exit_reason)
        finally:
            self.app.release_lock(user_id)

    def card(self, session_id: str) -> dict | None:
        return self._card(session_id)

    # ------------------------------------------------------------ conversation history
    def list_conversations(self, user_id: str, limit: int = 30, before=None) -> list[dict]:
        return self.history.list(user_id, limit, before)

    def get_conversation(self, user_id: str, session_id: str) -> dict | None:
        s = self.app.session(session_id)
        if not s or s["user_id"] != user_id or s.get("deleted_at"):
            return None
        msgs = self.app.messages(session_id)
        card = self._card(session_id)
        return {"conversation": {"id": s["id"], "title": s.get("title") or "New conversation",
                                 "title_source": s.get("title_source"), "state": s["state"],
                                 "created_at": s.get("created_at"), "message_count": len(msgs),
                                 "last_message_at": msgs[-1].get("created_at") if msgs else None,
                                 "has_pending_approval": card is not None},
                "messages": msgs, "approval_card": card, "options": self._options(session_id)}

    def select_slot(self, user_id: str, session_id: str, provider_id: str, slot_id: str) -> ChatResult:
        """The user tapped a slot bubble. Straight to the approval card: no model, no second confirmation.
        The same rules apply as for a typed request (the slot must come from the saved search; the hold decides the price)."""
        s = self.app.session(session_id)
        if not s or s["user_id"] != user_id or s.get("deleted_at"):
            return ChatResult(None, "I can't find that conversation.", None, exit_reason="not_found")
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return ChatResult(session_id, MSG_BUSY, s["state"], exit_reason="busy", busy=True)
        try:
            run, tracer = self._open_run(session_id)
            deps = self._deps(user_id, tracer)
            found = s["request_frame"].get("_search") or {}
            name = found.get("names", {}).get(provider_id, "that provider")
            label = when(found["times"][slot_id], found.get("ends", {}).get(slot_id)) if slot_id in found.get("times", {}) else "that slot"
            said = f"I'll take {name}, {label}"
            self.app.add_message(session_id, "user", said, run["id"])
            budget = RunBudget(self.clock, 1, self.max_tool_calls, timeout_s=self.timeout_s)
            try:
                gw = ToolGateway(deps, GatewayContext(session_id, user_id, said, auto_search=False), budget)
                out = gw.call("propose_book", {"provider_id": provider_id, "slot_id": slot_id})
                if out.ends_turn:
                    reply = PROPOSAL_TEXT["book"]
                elif out.model_view.get("status") == "price_too_different":
                    reply = (f"The final price came out far above the listed ₹{out.model_view['listed_price']}, "
                             "so I didn't hold it. Please pick another option.")
                else:  # the slot went, or the list was out of date: refresh it so they can pick again
                    gw.call("search_providers", {})
                    reply = "That slot is no longer available, so I've refreshed the list. Please pick another."
            except BudgetExceeded:
                reply = MSG_LIMIT
            self._close_run(run, session_id, reply, "done", budget)
            return self._result(session_id, reply, run, "done", options=self._options(session_id))
        finally:
            self.app.release_lock(user_id)

    def rename_conversation(self, user_id: str, session_id: str, title: str) -> str:
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return "busy"
        try:
            return self.history.rename(user_id, session_id, title)
        finally:
            self.app.release_lock(user_id)

    def delete_conversation(self, user_id: str, session_id: str) -> str:
        """Hide a conversation. Its open approval card is withdrawn and its slot hold released first, so a deleted
        chat can never leave something bookable behind."""
        s = self.app.session(session_id)
        if not s or s["user_id"] != user_id or s.get("deleted_at"):
            return "not_found"
        if not self.app.acquire_lock(user_id, self.clock.now(), LOCK_TTL_S):
            return "busy"
        try:
            approvals.invalidate_pending(self._deps(user_id, AppTracerNull()), session_id, reason="conversation_deleted")
            self.app.soft_delete_session(session_id, self.clock.now())
            return "deleted"
        finally:
            self.app.release_lock(user_id)

    def _move_quiet(self, deps: Deps, sid: str, to: str) -> None:
        frm = self.app.session(sid)["state"]
        if frm != to and to in states.ALLOWED[frm]:
            self.app.update_session(sid, state=to)
            deps.tracer.span("state_transition", f"{frm}->{to}")
