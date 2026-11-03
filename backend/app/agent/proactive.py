"""Proactive follow-up: a provider cancels, the agent notices without being asked, tells the user,
and lines up a replacement within what it knows about them.

Nothing in here books. Detection creates a notification and a *suggestion*. The approval card (fresh hold,
fresh approval) is only created when the user opens the proposal, so a card never expires unseen at 3am
and a slot is never held for someone who is asleep.
"""
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from app.agent.approvals import _fmt, render_card
from app.agent.budget import BudgetExceeded, RunBudget
from app.agent.gateway import GatewayContext, ToolGateway
from app.agent.trace import AppTracer
from app.clock import IST

log = logging.getLogger(__name__)

URGENT_WITHIN = timedelta(hours=24)
MAX_ATTEMPTS = 3
SLOT_LEN = timedelta(hours=2)
DEFAULT_LO, DEFAULT_HI = "06:00", "20:00"
BUDGET_KEY = {"plumber": "default_budget_plumber", "electrician": "default_budget_electrician",
              "ac_servicing": "default_budget_ac"}


@dataclass
class Decision:
    notify: bool
    reason: str
    urgency: str = "normal"
    replace: bool = True  # look for a replacement?


def decide(event: dict, booking: dict | None, others: list[dict], now: datetime) -> Decision:
    """Whether to tell the user, how urgently, and whether a replacement search makes sense."""
    if event["type"] != "provider_cancelled":
        return Decision(False, "unsupported_event")
    if not booking:
        return Decision(False, "unknown_booking")
    if booking["status"] != "cancelled_by_provider":
        return Decision(False, "booking_not_cancelled_by_provider")  # e.g. the user already cancelled it
    if booking["slot_start"] <= now:
        return Decision(False, "already_past")
    urgency = "urgent" if booking["slot_start"] - now <= URGENT_WITHIN else "normal"
    day = booking["slot_start"].astimezone(IST).date()
    if any(b["id"] != booking["id"] and b["status"] == "confirmed" and b["service"] == booking["service"]
           and b["slot_start"].astimezone(IST).date() == day for b in others):
        return Decision(True, "already_rebooked_same_day", urgency, replace=False)
    return Decision(True, "provider_cancelled", urgency, replace=True)


def budget_for(service: str, price: int, prefs: dict) -> int:
    """Saved default budget (never below what they already paid), else 20% above the old price."""
    saved = prefs.get(BUDGET_KEY.get(service, ""))
    if isinstance(saved, (int, float)) and saved > 0:
        return int(max(saved, price))
    return int(math.ceil(price * 1.2 / 10) * 10)


def ladder(start: datetime, end: datetime, prefs: dict) -> list[tuple[str, str, str, str]]:
    """Search windows, tightest first: near the old time, same day any time, then the next 3 days."""
    lo = max(DEFAULT_LO, prefs.get("earliest_time") or DEFAULT_LO)
    hi = min(DEFAULT_HI, prefs.get("latest_time") or DEFAULT_HI)
    s, e = start.astimezone(IST), end.astimezone(IST)
    day = s.date()
    near_lo = max(lo, (s - timedelta(hours=2)).strftime("%H:%M")) if (s - timedelta(hours=2)).date() == day else lo
    near_hi = min(hi, (e + timedelta(hours=2)).strftime("%H:%M")) if (e + timedelta(hours=2)).date() == day else hi
    rungs = []
    if near_lo < near_hi:
        rungs.append((day.isoformat(), day.isoformat(), near_lo, near_hi))
    if lo < hi:
        rungs.append((day.isoformat(), day.isoformat(), lo, hi))
        rungs.append((day.isoformat(), (day + timedelta(days=3)).isoformat(), lo, hi))
    return list(dict.fromkeys(rungs))


def choose_slot(frame: dict, original_start: str, prefer: tuple[str, str] | None = None) -> dict | None:
    """Top-ranked provider, and its slot closest to the time the user originally had."""
    s = frame.get("_search")
    if not s or not s.get("slots"):
        return None
    original = datetime.fromisoformat(original_start)
    if prefer and prefer[1] in s["slots"].get(prefer[0], []):
        pid, sid = prefer
    else:
        pid = next(iter(s["slots"]))
        sid = min(s["slots"][pid], key=lambda x: (abs(datetime.fromisoformat(s["times"][x]) - original), s["times"][x]))
    return {"provider_id": pid, "provider_name": s["names"][pid], "slot_id": sid, "start": s["times"][sid],
            "price": s["prices"][pid]}


class ProactiveProcessor:
    def __init__(self, app, store, clock, deps_factory):
        self.app, self.store, self.clock, self.deps_factory = app, store, clock, deps_factory

    # ------------------------------------------------------------ one event
    def process_safe(self, ev: dict) -> dict:
        """Never raises. A failed event goes back to pending, and is dropped after MAX_ATTEMPTS."""
        try:
            return self.process(ev)
        except Exception:
            log.exception("event %s failed", ev["id"])
            attempts = ev["payload"].get("_attempts", 0) + 1
            if attempts >= MAX_ATTEMPTS:
                self.store.update_event(ev["id"], status="ignored", processed_at=self.clock.now(),
                                        payload={**ev["payload"], "_attempts": attempts,
                                                 "ignored_reason": "processing_failed"})
            else:
                self.store.update_event(ev["id"], status="pending", payload={**ev["payload"], "_attempts": attempts})
            return {"event_id": ev["id"], "outcome": "error"}

    def process(self, ev: dict) -> dict:
        now = self.clock.now()
        done = self.app.notification_for_event(ev["id"])
        if done:  # retried after a crash: the notification already exists
            self.store.update_event(ev["id"], status="done", processed_at=now)
            return {"event_id": ev["id"], "outcome": "duplicate", "notification_id": done["id"]}
        booking = self.store.booking(ev["booking_id"])
        others = self.store.bookings_for_user(booking["user_id"]) if booking else []
        decision = decide(ev, booking, others, now)
        if not decision.notify:
            self.store.update_event(ev["id"], status="ignored", processed_at=now,
                                    payload={**ev["payload"], "ignored_reason": decision.reason})
            return {"event_id": ev["id"], "outcome": "ignored", "reason": decision.reason}
        return self._notify(ev, booking, decision)

    def _notify(self, ev: dict, booking: dict, decision: Decision) -> dict:
        now, user_id = self.clock.now(), booking["user_id"]
        provider = self.store.provider(booking["provider_id"])
        session = self.app.create_session(user_id)
        self.app.set_title(session["id"], f"{booking['service'].replace('_', ' ').capitalize()}: provider cancelled", "system")
        run = self.app.create_run(session["id"], "event", now + timedelta(seconds=45), now)
        tracer = AppTracer(self.app, run["id"])
        tracer.span("event", ev["type"], input={"event_id": ev["id"], "booking_id": booking["id"]},
                    output={"decision": asdict(decision)})
        suggestion, budget = None, RunBudget(self.clock, max_turns=1, max_tool_calls=12, reserve_for_reconcile=0)
        if decision.replace:
            try:
                suggestion = self._find_replacement(user_id, session["id"], booking, provider, tracer, budget)
            except BudgetExceeded:
                tracer.span("event", "replacement_search", error="budget_exceeded")
        body = self._compose(booking, provider, decision, suggestion)
        self.app.add_message(session["id"], "assistant", body, run["id"])
        note = self.app.add_notification(user_id, session["id"], body, now, event_id=ev["id"], urgency=decision.urgency)
        self.store.update_event(ev["id"], status="done", processed_at=now, run_id=run["id"])
        self.app.update_run(run["id"], status="done", exit_reason="done", final_message=body, ended_at=now,
                            turns=0, tool_calls=budget.tool_calls)
        return {"event_id": ev["id"], "outcome": "notified", "session_id": session["id"], "run_id": run["id"],
                "notification_id": note["id"], "urgency": decision.urgency, "has_replacement": suggestion is not None,
                "body": body}

    # ------------------------------------------------------ replacement search
    def _find_replacement(self, user_id, session_id, booking, provider, tracer, budget) -> dict | None:
        deps = self.deps_factory(user_id, tracer)
        gw = ToolGateway(deps, GatewayContext(session_id, user_id, "", auto_search=False), budget)
        prefs = {m["key"]: m["value"] for m in self.app.active_memories(user_id)}
        slot = self.store.slot(booking["slot_id"]) if booking["slot_id"] else None
        start = booking["slot_start"]
        end = slot["end_at"] if slot else start + SLOT_LEN
        # the cancelled provider is never the replacement
        self.app.update_session(session_id, request_frame={"_exclude": [booking["provider_id"]]})
        cap = budget_for(booking["service"], booking["price"], prefs)
        for n, (d0, d1, ws, we) in enumerate(ladder(start, end, prefs), 1):
            upd = gw.call("update_request_frame", {
                "service": booking["service"], "area": provider["area"], "date_start": d0, "date_end": d1,
                "window_start": ws, "window_end": we, "budget_max": cap})
            if upd.error:
                tracer.span("event", "replacement_search", input={"rung": n}, error=upd.error)
                continue
            res = gw.call("search_providers", {})
            tracer.span("event", "replacement_search", input={"rung": n, "dates": [d0, d1], "window": [ws, we],
                                                              "budget": cap},
                        output={"status": res.model_view.get("status")})
            if res.model_view.get("status") != "ok":
                continue
            frame = self.app.session(session_id)["request_frame"]
            pick = choose_slot(frame, start.isoformat())
            frame["_suggestion"] = {**pick, "rung": n}
            frame["_replace_for"] = {"booking_id": booking["id"], "original_start": start.isoformat(),
                                     "provider_id": booking["provider_id"]}
            self.app.update_session(session_id, request_frame=frame)
            return frame["_suggestion"]
        return None

    @staticmethod
    def _compose(booking, provider, decision, sug) -> str:
        what = booking["service"].replace("_", " ")
        head = f"{'Urgent: ' if decision.urgency == 'urgent' else ''}{provider['name']} cancelled your {what} " \
               f"on {_fmt(booking['slot_start'].isoformat())}."
        if not decision.replace:
            return head + f" You already have another {what} booked that day, so I haven't looked for a replacement."
        if not sug:
            return head + (" I couldn't find a replacement within your budget and preferred times. "
                           "Tell me if you'd like me to widen the search.")
        more = " (more than before)" if sug["price"] > booking["price"] else ""
        return (head + f" I found a replacement: {sug['provider_name']}, {_fmt(sug['start'])}, ₹{sug['price']}{more}. "
                       "Nothing is booked yet. Open it to review and approve.")

    # ------------------------------------------------------- user opens it
    def open_proposal(self, user_id: str, session_id: str, tracer, budget: RunBudget) -> tuple[str, str]:
        """Turn the suggestion into a real card: fresh hold, fresh approval. Returns (reply, outcome)."""
        s = self.app.session(session_id)
        sug, rf = s["request_frame"].get("_suggestion"), s["request_frame"].get("_replace_for")
        if not sug or not rf:
            return ("There's no replacement lined up for this one. Tell me what you'd like and I'll search.",
                    "no_suggestion")
        a = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
        if a and a["status"] == "pending" and self.clock.now() < a["expires_at"]:
            return "Here's the replacement I'd book. Review it and tap Approve to confirm.", "existing"
        gw = ToolGateway(self.deps_factory(user_id, tracer), GatewayContext(session_id, user_id, ""), budget)
        prefer = (sug["provider_id"], sug["slot_id"])
        for _ in range(2):  # the slot may have gone while the user was away: re-search once
            res = gw.call("search_providers", {})
            if res.model_view.get("status") != "ok":
                break
            pick = choose_slot(self.app.session(session_id)["request_frame"], rf["original_start"], prefer)
            out = gw.call("propose_book", {"provider_id": pick["provider_id"], "slot_id": pick["slot_id"]})
            if out.ends_turn:
                return "Here's the replacement I'd book. Review it and tap Approve to confirm.", "proposed"
            if out.model_view.get("status") == "price_too_different":
                return (f"The final price came out at ₹{out.model_view['final_price']}, far above the listed "
                        f"₹{out.model_view['listed_price']}, so I didn't hold it. Tell me if you'd like to try another option.",
                        "price_too_different")
            prefer = None
        frame = self.app.session(session_id)["request_frame"]
        frame.pop("_suggestion", None)
        self.app.update_session(session_id, request_frame=frame)
        return ("That replacement is gone and I couldn't find another in your range. "
                "Tell me if you'd like me to widen the search.", "gone")

    def card(self, session_id: str) -> dict | None:
        s = self.app.session(session_id)
        a = self.app.approval(s["pending_approval_id"]) if s["pending_approval_id"] else None
        return render_card(a) if a and a["status"] == "pending" else None
