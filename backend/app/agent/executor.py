"""Runs an approved action. No LLM in here: plain code, write-ahead logging, and a source of truth.

Rules this file exists to keep:
  1. consume the approval and record the attempt in ONE atomic step, before any tool call
  2. the idempotency key comes from the approval, so every retry reuses it
  3. after a timeout, ask get_booking_status; never assume
  4. never say "booked" until get_booking_status confirms it
"""
import time
from dataclasses import dataclass

from app.agent import states
from app.agent.app_store import ApprovalNotConsumable
from app.agent.approvals import _fmt
from app.agent.budget import BudgetExceeded, RunBudget
from app.agent.deps import Deps
from app.tools.errors import ToolTimeout, ToolUnavailable

AMBIGUOUS = (ToolTimeout, ToolUnavailable)

MSG_UNKNOWN = ("I couldn't confirm whether that went through, so I'm not telling you it did. "
               "I'll keep checking and will message you as soon as I know.")
MSG_SLOT_TAKEN = "That slot was just taken by someone else, so nothing was booked. Want me to look at other options?"
MSG_HOLD_EXPIRED = "The hold on that slot ran out before it could be booked, so nothing was booked. Want me to search again?"
MSG_CONFLICT = "That new slot is no longer available. Your original booking is unchanged."
MSG_FAILED = "That didn't go through and nothing was changed. Want me to try something else?"


@dataclass
class ExecResult:
    outcome: str  # booked|cancelled|rescheduled|already_cancelled|slot_taken|hold_expired|slot_conflict|failed|unknown
    message: str
    booking_id: str | None = None


_SUCCESS = {"booked", "cancelled", "rescheduled"}
_BACK_TO_SEARCH = {"slot_taken", "hold_expired", "slot_conflict", "failed", "already_cancelled"}


class Executor:
    def __init__(self, deps: Deps, sleep=time.sleep, max_retries: int = 2):
        self.d, self.sleep, self.max_retries = deps, sleep, max_retries
        self.budget: RunBudget | None = None

    # ------------------------------------------------------------ entry points
    def run(self, approval: dict) -> ExecResult:
        """First execution of an approved approval."""
        try:
            attempt = self.d.app.begin_attempt(approval["id"], approval["payload_hash"],
                                               approval["payload"].get("hold_id"), self.d.clock.now())
        except ApprovalNotConsumable:
            return ExecResult("failed", "That approval can't be used any more (already used, changed or expired), "
                                        "so I did nothing.")
        self._move(approval["session_id"], "EXECUTE")
        return self._execute(approval, attempt, check_first=False)

    def resume(self, approval: dict, attempt: dict) -> ExecResult:
        """Finish an attempt left unresolved by a crash or an unclear outcome. Asks the source of truth first."""
        return self._execute(approval, attempt, check_first=True)

    # ------------------------------------------------------------------ core
    def _execute(self, a: dict, att: dict, check_first: bool) -> ExecResult:
        sid = a["session_id"]
        self.budget = RunBudget(self.d.clock, max_tool_calls=12, reserve_for_reconcile=0, timeout_s=45)
        if self.d.app.session(sid)["state"] != "EXECUTE":
            self._try_move(sid, "EXECUTE")
        try:
            res = {"book": self._book, "cancel": self._cancel, "reschedule": self._reschedule}[a["action"]](
                a, att, check_first)
        except BudgetExceeded:
            res = ExecResult("unknown", MSG_UNKNOWN)
        except Exception:  # never claim success on an unexpected failure
            self.d.tracer.span("tool_call", "executor.error", error="unexpected")
            res = ExecResult("unknown", MSG_UNKNOWN)
        self._finish(a, att, res)
        return res

    def _call(self, name: str, fn, *args, **kw):
        self.budget.charge_tool(reconcile=True)
        try:
            out = fn(*args, **kw)
        except AMBIGUOUS as e:
            self.d.tracer.span("tool_call", f"exec.{name}", input={"args": list(args), **kw}, error=type(e).__name__)
            raise
        self.d.tracer.span("tool_call", f"exec.{name}", input={"args": list(args), **kw}, output=out)
        return out

    def _try(self, name: str, fn, *args, **kw):
        try:
            return self._call(name, fn, *args, **kw), False
        except AMBIGUOUS:
            return None, True

    def _status(self, **kw) -> dict:
        return self._call("get_booking_status", self.d.tools.get_booking_status, **kw)

    # ------------------------------------------------------------------ book
    def _book(self, a: dict, att: dict, check_first: bool) -> ExecResult:
        sid, hold, key = a["session_id"], a["payload"]["hold_id"], att["idempotency_key"]
        ambiguous, retries = check_first, 0
        while True:
            if not ambiguous:
                r, ambiguous = self._try("book", self.d.tools.book, hold, key)
                if not ambiguous:
                    if r["status"] == "success":
                        return self._verify_booked(a, key)
                    if r["status"] == "slot_taken":
                        return ExecResult("slot_taken", MSG_SLOT_TAKEN)
                    if r["status"] == "hold_expired":
                        return ExecResult("hold_expired", MSG_HOLD_EXPIRED)
                    return ExecResult("failed", MSG_FAILED)
            # ambiguous (timeout/503, or a resumed attempt): the source of truth decides
            self._try_move(sid, "RECONCILE")
            st = self._status(hold_id=hold, idempotency_key=key)
            if st["status"] == "booked":
                return self._verify_booked(a, key)
            if st["status"] == "failed":
                return ExecResult("slot_taken", MSG_SLOT_TAKEN)
            if st.get("hold") in ("expired", "released"):
                return ExecResult("hold_expired", MSG_HOLD_EXPIRED)
            if st.get("hold") == "active" and retries < self.max_retries:
                retries += 1
                self.d.app.update_attempt(att["id"], retry_count=att["retry_count"] + retries)
                self._try_move(sid, "EXECUTE")
                self.sleep(0.2 * 2 ** retries)
                ambiguous = False  # retry with the SAME key
                continue
            return ExecResult("unknown", MSG_UNKNOWN)

    def _verify_booked(self, a: dict, key: str) -> ExecResult:
        st = self._status(idempotency_key=key)
        if st["status"] != "booked" or not st.get("booking_id"):
            return ExecResult("unknown", MSG_UNKNOWN)  # book said success but the source of truth disagrees
        p = a["payload"]
        return ExecResult("booked", f"Booked and verified: {p['provider_name']}, {_fmt(p['slot_start'])}, "
                                    f"₹{st['price']}. Booking ID {st['booking_id']}.", st["booking_id"])

    # ---------------------------------------------------------------- cancel
    def _cancel(self, a: dict, att: dict, check_first: bool) -> ExecResult:
        p, key = a["payload"], att["idempotency_key"]
        if check_first and self._applied(key, "cancelled"):
            return self._cancelled(p)
        for i in range(self.max_retries + 1):
            r, ambiguous = self._try("cancel", self.d.tools.cancel, p["booking_id"], key)
            if not ambiguous:
                if r["status"] == "cancelled":
                    return self._cancelled(p) if self._applied(key, "cancelled") else ExecResult("unknown", MSG_UNKNOWN)
                if r["status"] == "already_cancelled":
                    return ExecResult("already_cancelled", "That booking was already cancelled, so nothing changed.")
                return ExecResult("failed", MSG_FAILED)
            if self._applied(key, "cancelled"):  # timeout but it landed
                return self._cancelled(p)
            self.sleep(0.2 * 2 ** i)
        return ExecResult("unknown", MSG_UNKNOWN)

    def _applied(self, key: str, status: str) -> bool:
        st = self._status(idempotency_key=key)
        return bool(st.get("applied")) and st.get("status") == status

    @staticmethod
    def _cancelled(p: dict) -> ExecResult:
        return ExecResult("cancelled", f"Cancelled and verified: {p['provider_name']}, {_fmt(p['slot_start'])}.",
                          p["booking_id"])

    # ------------------------------------------------------------ reschedule
    def _reschedule(self, a: dict, att: dict, check_first: bool) -> ExecResult:
        p, key = a["payload"], att["idempotency_key"]
        done = ExecResult("rescheduled", f"Rescheduled and verified: {p['provider_name']}, now {_fmt(p['to_start'])}.",
                          p["booking_id"])
        if check_first and self._applied(key, "rescheduled"):
            return done
        for i in range(self.max_retries + 1):
            r, ambiguous = self._try("reschedule", self.d.tools.reschedule, p["booking_id"], p["to_slot_id"], key)
            if not ambiguous:
                if r["status"] == "rescheduled":
                    return done if self._applied(key, "rescheduled") else ExecResult("unknown", MSG_UNKNOWN)
                if r["status"] == "slot_conflict":
                    return ExecResult("slot_conflict", MSG_CONFLICT)
                return ExecResult("failed", MSG_FAILED)
            if self._applied(key, "rescheduled"):
                return done
            self.sleep(0.2 * 2 ** i)
        return ExecResult("unknown", MSG_UNKNOWN)

    # ---------------------------------------------------------------- finish
    def _finish(self, a: dict, att: dict, res: ExecResult) -> None:
        sid, now = a["session_id"], self.d.clock.now()
        unresolved = res.outcome == "unknown"
        self.d.app.update_attempt(
            att["id"], status="unknown" if unresolved else "succeeded" if res.outcome in _SUCCESS else "failed",
            booking_id=res.booking_id, tool_response={"outcome": res.outcome}, resolved_at=None if unresolved else now)
        session = self.d.app.session(sid)
        frame = session["request_frame"]
        if res.outcome in _BACK_TO_SEARCH:
            frame.pop("_search", None)  # stale after a failure
            frame.pop("_avail", None)
        self.d.app.update_session(sid, request_frame=frame, pending_approval_id=None, active_hold_id=None)
        self._try_move(sid, "RECONCILE" if unresolved else "DONE" if res.outcome in _SUCCESS else "SEARCH")

    def _move(self, sid: str, to: str) -> None:
        frm = self.d.app.session(sid)["state"]
        if frm != to:
            states.check(frm, to)
            self.d.app.update_session(sid, state=to)
            self.d.tracer.span("state_transition", f"{frm}->{to}")

    def _try_move(self, sid: str, to: str) -> None:
        try:
            self._move(sid, to)
        except states.InvalidTransition:
            pass
