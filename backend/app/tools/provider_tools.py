"""The eight mock provider tools from the assignment.

Domain outcomes come back as dicts with a "status". Infrastructure failures raise:
ToolTimeout (outcome unknown) and ToolUnavailable (503). One instance is bound to one
user, so a user can never touch another user's bookings.
"""
import random
import uuid
from datetime import datetime, timedelta

from app.clock import IST, Clock
from app.tools.config import ScenarioConfig
from app.tools.day_schedule import DayScenario, day_scenario
from app.tools.errors import ToolTimeout, ToolUnavailable
from app.tools.store import Store

MAX_PROVIDERS = 8
MAX_SLOTS_PER_PROVIDER = 60  # a full fortnight of slots is 15 days x 5 windows


def _to_dt(v, end: bool = False) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=IST)
    s = str(v)
    if len(s) == 10:  # date only: whole day, end is inclusive
        d = datetime.fromisoformat(s).replace(tzinfo=IST)
        return d + timedelta(days=1) if end else d
    d = datetime.fromisoformat(s)
    return d if d.tzinfo else d.replace(tzinfo=IST)


def _range(date_range) -> tuple[datetime, datetime]:
    s, e = date_range if isinstance(date_range, (list, tuple)) else (date_range["start"], date_range["end"])
    return _to_dt(s), _to_dt(e, end=True)


def _slot_id(slot) -> str:
    return slot["id"] if isinstance(slot, dict) else str(slot)


def _slot_out(s: dict) -> dict:
    return {"id": s["id"], "start": s["start_at"].isoformat(), "end": s["end_at"].isoformat()}


class ProviderTools:
    def __init__(self, store: Store, clock: Clock, config: ScenarioConfig | None = None,
                 user_id: str = "user_demo"):
        self.s, self.clock, self.user_id = store, clock, user_id
        self.cfg = config or ScenarioConfig()  # behaviour comes from the day schedule, never from settings or the DB

    @staticmethod
    def _day(slot: dict) -> DayScenario:
        """Which situation this booking is in: decided by the day of the slot (Asia/Kolkata)."""
        return day_scenario(slot["start_at"].astimezone(IST).date())

    def _rng(self, *parts) -> random.Random:
        return random.Random(":".join(str(p) for p in (self.cfg.seed, *parts)))

    # ---------- read tools ----------
    def search_providers(self, service: str, area: str, date_range) -> dict:
        now = self.clock.now()
        self.s.expire_holds(now)
        start, end = _range(date_range)
        start = max(start, now)
        provs = self.s.providers(service.strip().lower().replace(" ", "_"), area.strip())
        provs.sort(key=lambda p: -p["rating"])
        out = []
        for p in provs[:MAX_PROVIDERS]:
            slots = self.s.free_slots(p["id"], start, end)[:MAX_SLOTS_PER_PROVIDER]
            out.append({"id": p["id"], "name": p["name"], "service": p["service"], "area": p["area"],
                        "rating": float(p["rating"]), "price": p["base_price"],
                        "attributes": p["attributes"], "slots": [_slot_out(s) for s in slots]})
        return {"providers": out}

    def get_provider(self, provider_id: str) -> dict:
        p = self.s.provider(provider_id)
        if not p:
            return {"status": "not_found"}
        return {"id": p["id"], "name": p["name"], "service": p["service"], "area": p["area"],
                "rating": float(p["rating"]), "price": p["base_price"], "attributes": p["attributes"],
                "reviews": [{"author": r["author"], "text": r["text"]} for r in self.s.reviews(provider_id)]}

    def list_bookings(self, user_id: str | None = None) -> dict:
        if (user_id or self.user_id) != self.user_id:
            return {"bookings": []}
        rows = []
        for b in self.s.bookings_for_user(self.user_id):
            p = self.s.provider(b["provider_id"]) or {}
            rows.append({"id": b["id"], "provider_id": b["provider_id"], "provider_name": p.get("name"),
                         "service": b["service"], "slot": b["slot_start"].isoformat(),
                         "status": b["status"], "price": b["price"]})
        return {"bookings": rows}

    def get_booking_status(self, hold_id: str | None = None, idempotency_key: str | None = None) -> dict:
        """Source of truth for ambiguous outcomes (timeouts)."""
        if not (hold_id or idempotency_key):
            return {"status": "error", "code": "need_hold_id_or_idempotency_key"}
        b = self.s.booking_by(hold_id=hold_id, idempotency_key=idempotency_key)
        if b:
            st = "booked" if b["status"] in ("confirmed", "completed") else b["status"]
            return {"status": st, "booking_id": b["id"], "provider_id": b["provider_id"],
                    "slot_id": b["slot_id"], "price": b["price"]}
        if idempotency_key:
            rec = self.s.idem(idempotency_key)
            if rec:
                r = rec["response"]
                if r.get("status") == "slot_taken":
                    return {"status": "failed", "reason": "slot_taken"}
                if rec["tool"] in ("cancel", "reschedule"):
                    return {**r, "applied": True, "tool": rec["tool"]}
        if hold_id:
            h = self.s.hold(hold_id)
            if not h:
                return {"status": "not_found"}
            if h["status"] == "active" and self.clock.now() >= h["expires_at"]:
                self.s.expire_holds(self.clock.now())
                h = self.s.hold(hold_id)
            return {"status": "not_booked", "hold": h["status"], "expires_at": h["expires_at"].isoformat()}
        return {"status": "not_found"}

    # ---------- hold ----------
    def hold_slot(self, provider_id: str, slot) -> dict:
        now = self.clock.now()
        self.s.expire_holds(now)
        sid = _slot_id(slot)
        p = self.s.provider(provider_id)
        if not p:
            return {"status": "not_found", "code": "unknown_provider"}
        sl = self.s.slot(sid)
        if not sl or sl["provider_id"] != provider_id or sl["start_at"] <= now:
            return {"status": "slot_unavailable"}
        if not self.s.claim_slot(sid, "free", "held"):
            return {"status": "slot_unavailable"}
        if self.cfg.price_drift_prob is None:
            drift = self._day(sl).quote_extra  # the day decides
        else:  # test hook
            drift = self.cfg.price_drift if self._rng("drift", sid).random() < self.cfg.price_drift_prob else 0
        hold = {"id": f"hold_{uuid.uuid4().hex[:10]}", "provider_id": provider_id, "slot_id": sid,
                "quoted_price": p["base_price"] + drift, "status": "active",
                "expires_at": now + timedelta(minutes=self.cfg.hold_minutes)}
        self.s.add_hold(hold)
        return {"status": "held", "hold_id": hold["id"], "provider_id": provider_id, "slot_id": sid,
                "quoted_price": hold["quoted_price"], "expires_at": hold["expires_at"].isoformat()}

    # ---------- side effects ----------
    def _outcome(self, day: DayScenario, attempt: int) -> str:
        seq = self.cfg.forced_book_outcomes or day.book  # a forced outcome is a test hook; otherwise the day decides
        return seq[min(attempt, len(seq) - 1)]

    def book(self, hold_id: str, idempotency_key: str) -> dict:
        rec = self.s.idem(idempotency_key)
        if rec:
            return {**rec["response"], "replayed": True}
        h = self.s.hold(hold_id)
        if not h:
            return {"status": "error", "code": "unknown_hold"}
        if h["status"] == "booked":
            return {"status": "error", "code": "hold_already_used"}
        if h["status"] == "released":
            return {"status": "slot_taken"}
        now = self.clock.now()
        if h["status"] == "expired" or now >= h["expires_at"]:
            self.s.expire_holds(now)
            return {"status": "hold_expired"}

        attempt = self.s.next_attempt(f"book:{hold_id}")
        outcome = self._outcome(self._day(self.s.slot(h["slot_id"])), attempt)
        if outcome == "slot_taken":
            self.s.set_slot_status(h["slot_id"], "booked")  # someone else got it
            self.s.update_hold(hold_id, status="released")
            resp = {"status": "slot_taken"}
            self.s.put_idem(idempotency_key, "book", resp)
            return resp
        if outcome == "timeout_fail":
            raise ToolTimeout("book timed out")

        p = self.s.provider(h["provider_id"])
        sl = self.s.slot(h["slot_id"])
        booking_id = f"bk_{uuid.uuid4().hex[:8]}"
        self.s.add_booking({"id": booking_id, "user_id": self.user_id, "provider_id": h["provider_id"],
                            "slot_id": h["slot_id"], "service": p["service"], "slot_start": sl["start_at"],
                            "status": "confirmed", "price": h["quoted_price"], "hold_id": hold_id,
                            "idempotency_key": idempotency_key, "created_at": now})
        self.s.set_slot_status(h["slot_id"], "booked")
        self.s.update_hold(hold_id, status="booked")
        resp = {"status": "success", "booking_id": booking_id, "provider_id": h["provider_id"],
                "slot_id": h["slot_id"], "price": h["quoted_price"], "hold_id": hold_id}
        self.s.put_idem(idempotency_key, "book", resp)
        if outcome == "timeout_success":
            raise ToolTimeout("book timed out")  # it DID book; caller must check status
        return resp

    def cancel(self, booking_id: str, idempotency_key: str) -> dict:
        rec = self.s.idem(idempotency_key)
        if rec:
            return {**rec["response"], "replayed": True}
        if self.cfg.cancel_first_503 and self.s.next_attempt(f"cancel:{idempotency_key}") == 0:
            raise ToolUnavailable("503 service unavailable")
        b = self.s.booking(booking_id)
        if not b or b["user_id"] != self.user_id:
            return {"status": "not_found"}
        if b["status"] in ("cancelled", "cancelled_by_provider"):
            return {"status": "already_cancelled", "booking_id": booking_id}
        if b["status"] == "completed" or b["slot_start"] <= self.clock.now():
            return {"status": "not_cancellable", "booking_id": booking_id}
        self.s.update_booking(booking_id, status="cancelled")
        if b["slot_id"]:
            self.s.set_slot_status(b["slot_id"], "free")
        resp = {"status": "cancelled", "booking_id": booking_id}
        self.s.put_idem(idempotency_key, "cancel", resp)
        return resp

    def reschedule(self, booking_id: str, slot, idempotency_key: str) -> dict:
        rec = self.s.idem(idempotency_key)
        if rec:
            return {**rec["response"], "replayed": True}
        b = self.s.booking(booking_id)
        if not b or b["user_id"] != self.user_id:
            return {"status": "not_found"}
        if b["status"] != "confirmed":
            return {"status": "not_reschedulable", "booking_status": b["status"]}
        sid = _slot_id(slot)
        new = self.s.slot(sid)
        if not new or new["provider_id"] != b["provider_id"] or sid == b["slot_id"] \
                or new["start_at"] <= self.clock.now():
            return {"status": "invalid_slot"}
        if sid in self.cfg.reschedule_conflict_slots or self._day(new).reschedule_conflict                 or not self.s.claim_slot(sid, "free", "booked"):
            return {"status": "slot_conflict", "booking_id": booking_id}  # old booking untouched
        if b["slot_id"]:
            self.s.set_slot_status(b["slot_id"], "free")
        self.s.update_booking(booking_id, slot_id=sid, slot_start=new["start_at"])
        resp = {"status": "rescheduled", "booking_id": booking_id, "slot": _slot_out(new)}
        self.s.put_idem(idempotency_key, "reschedule", resp)
        return resp
