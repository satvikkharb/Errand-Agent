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
from app.agent.tokens import count_tokens
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


class AppTracer:
    """Writes spans to the app store under one run id."""

    def __init__(self, app, run_id: str):
        self.app, self.run_id = app, run_id

    def span(self, kind, name, input=None, output=None, latency_ms=None, error=None, **extra):
        self.app.add_span(self.run_id, {"kind": kind, "name": name, "input": input, "output": output,
                                        "latency_ms": latency_ms, "error": error, **extra})


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", "", s.lower()).split())


def guard_reply(text: str, has_pending: bool) -> str:
    """The model can't book, so it must not claim it did. Replace such claims with the truth."""
    if _CLAIM.search(text) and not _HEDGE.search(text):
        return MSG_NO_CLAIM.format(tail="Tap Approve on the card when you're ready." if has_pending
                                   else "I can set it up for your approval if you'd like.")
    return text


class Orchestrator:
    def __init__(self, app, store, clock, llm: LLM, *, builder: ContextBuilder | None = None, max_turns: int = 8,
                 max_tool_calls: int = 12, timeout_s: int = 45, sleep=time.sleep, tools_config=None,
                 allow_typed_approval: bool = True, price_in_per_1m: float = 0.40, price_out_per_1m: float = 1.60):
        self.app, self.store, self.clock, self.llm = app, store, clock, llm
        self.builder = builder or ContextBuilder()
        self.max_turns, self.max_tool_calls, self.timeout_s = max_turns, max_tool_calls, timeout_s
        self.sleep, self.tools_config, self.allow_typed = sleep, tools_config, allow_typed_approval
        self.price_in, self.price_out = price_in_per_1m, price_out_per_1m
        self.specs = tool_specs()
        self.tools_tokens = count_tokens(json.dumps(self.specs))

    # ------------------------------------------------------------------ wiring
    def _deps(self, user_id: str, tracer) -> Deps:
        tools = ProviderTools(self.store, self.clock, self.tools_config, user_id=user_id)
        return Deps(self.app, self.store, tools, self.clock, tracer)

    def _open_run(self, session_id: str):
        run = self.app.create_run(session_id, "user_message",
                                  self.clock.now() + timedelta(seconds=self.timeout_s), self.clock.now())
        return run, AppTracer(self.app, run["id"])

    def _close_run(self, run, session_id, reply, exit_reason, budget=None):
        self.app.add_message(session_id, "assistant", reply, run["id"])
        self.app.update_run(run["id"], status="failed" if exit_reason == "error" else "done",
                            exit_reason=exit_reason, final_message=reply, ended_at=self.clock.now(),
                            turns=budget.turns if budget else 0, tool_calls=budget.tool_calls if budget else 0)

    def _card(self, session_id) -> dict | None:
        s = self.app.session(session_id)
        a = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
        return approvals.render_card(a) if a and a["status"] == "pending" else None

    def _result(self, session_id, reply, run, exit_reason, views=(), notices=()):
        s = self.app.session(session_id)
        return ChatResult(session_id, reply, s["state"], run["id"], exit_reason, self._card(session_id),
                          list(views), list(notices))

    # ------------------------------------------------------------ chat message
    def handle_message(self, user_id: str, message: str, session_id: str | None = None) -> ChatResult:
        message = (message or "").strip()
        if not message or len(message) > MAX_MESSAGE_CHARS:
            return ChatResult(session_id, MSG_EMPTY if not message else
                              f"That message is too long. Please keep it under {MAX_MESSAGE_CHARS} characters.",
                              None, exit_reason="rejected_input")
        if session_id:
            s = self.app.session(session_id)
            if not s or s["user_id"] != user_id:
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
        notices = self._reconcile(user_id, deps)
        msg_row = self.app.add_message(sid, "user", message, run["id"])
        budget = RunBudget(self.clock, self.max_turns, self.max_tool_calls, timeout_s=self.timeout_s)
        views: list = []
        try:
            typed = self._typed_decision(deps, sid, user_id, message)
            if typed is not None:
                reply, exit_reason = typed, "done"
            else:
                reply, exit_reason = self._agent_loop(deps, user_id, sid, msg_row, budget, tracer, views)
        except BudgetExceeded as e:
            reply, exit_reason = MSG_LIMIT, e.reason
            tracer.span("state_transition", "run_limit", error=e.reason)
        except Exception:
            log.exception("run failed")
            reply, exit_reason = MSG_ERROR, "error"
        if exit_reason == "done" and not views:
            reply = guard_reply(reply, self._card(sid) is not None)
        self._close_run(run, sid, reply, exit_reason, budget)
        return self._result(sid, reply, run, exit_reason, views, notices)

    def _typed_decision(self, deps: Deps, sid: str, user_id: str, message: str) -> str | None:
        """A bare 'yes'/'no' answers the ONE pending card. Anything else goes to the model."""
        s = self.app.session(sid)
        if not (self.allow_typed and s["pending_approval_id"]):
            return None
        n = _norm(message)
        if n in AFFIRM:
            return self._approve_inner(deps, user_id, s["pending_approval_id"])
        if n in REJECT:
            res = approvals.reject(deps, s["pending_approval_id"], user_id)
            if res["ok"]:
                self._move_quiet(deps, sid, "SEARCH")
                return "Okay, I won't do that. Tell me what you'd like instead."
        return None

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
            reply = self._approve_inner(deps, user_id, approval_id)
            self._close_run(run, s["id"], reply, "done")
            return self._result(s["id"], reply, run, "done", notices=notices)
        finally:
            self.app.release_lock(user_id)

    def _approve_inner(self, deps: Deps, user_id: str, approval_id: str) -> str:
        res = approvals.approve(deps, approval_id, user_id)
        if not res["ok"]:
            a = self.app.approval(approval_id)
            if res["error"] in ("approval_expired", "hold_expired") and a:  # clean up and unstick the session
                approvals.invalidate_pending(deps, a["session_id"], reason=res["error"])
                self._move_quiet(deps, a["session_id"], "SEARCH")
            return APPROVAL_ERRORS.get(res["error"], "I couldn't use that approval, so I did nothing.")
        out = Executor(deps, sleep=self.sleep).run(res["approval"])
        return out.message

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
    def _reconcile(self, user_id: str | None, deps: Deps) -> list[str]:
        """Resolve attempts left 'started'/'unknown' by a crash or an unclear outcome. Runs lazily on the next
        interaction (and from recover()). Returns notices to show the user."""
        notices = []
        for att in self.app.unresolved_attempts(user_id):
            a = self.app.approval(att["approval_id"])
            s = self.app.session(a["session_id"])
            d = self._deps(s["user_id"], deps.tracer)
            res = Executor(d, sleep=self.sleep).resume(a, att)
            if res.outcome != "unknown":
                body = res.message
                self.app.add_notification(s["user_id"], s["id"], body, self.clock.now())
                notices.append(body)
        return notices

    def recover(self) -> list[str]:
        """Process-start hook: finish every unresolved attempt for every user."""
        run_tracer = type("T", (), {"span": staticmethod(lambda *a, **k: None)})()
        return self._reconcile(None, Deps(self.app, self.store, None, self.clock, run_tracer))

    def _move_quiet(self, deps: Deps, sid: str, to: str) -> None:
        frm = self.app.session(sid)["state"]
        if frm != to and to in states.ALLOWED[frm]:
            self.app.update_session(sid, state=to)
            deps.tracer.span("state_transition", f"{frm}->{to}")
