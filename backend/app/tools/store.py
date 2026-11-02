"""Storage behind the provider tools. Two backends: InMemoryStore (tests/evals), SupabaseStore (app).

Rows are plain dicts. Datetime fields (start_at, end_at, expires_at, slot_start) are tz-aware datetimes.
"""
import uuid
from datetime import datetime
from typing import Protocol

from app.tools import seed_data


class Store(Protocol):
    def config(self) -> dict | None: ...
    def areas(self) -> list[str]: ...  # areas we have providers in
    def providers(self, service: str, area: str) -> list[dict]: ...
    def provider(self, pid: str) -> dict | None: ...
    def reviews(self, pid: str) -> list[dict]: ...
    def free_slots(self, pid: str, start: datetime, end: datetime) -> list[dict]: ...
    def slot(self, sid: str) -> dict | None: ...
    def claim_slot(self, sid: str, frm: str, to: str) -> bool: ...  # atomic compare-and-set
    def set_slot_status(self, sid: str, status: str) -> None: ...
    def add_hold(self, hold: dict) -> None: ...
    def hold(self, hid: str) -> dict | None: ...
    def update_hold(self, hid: str, **fields) -> None: ...
    def expire_holds(self, now: datetime) -> None: ...
    def add_booking(self, booking: dict) -> None: ...
    def booking(self, bid: str) -> dict | None: ...
    def update_booking(self, bid: str, **fields) -> None: ...
    def booking_by(self, hold_id: str | None = None, idempotency_key: str | None = None) -> dict | None: ...
    def bookings_for_user(self, user_id: str) -> list[dict]: ...
    def idem(self, key: str) -> dict | None: ...  # {"key","tool","response"}
    def put_idem(self, key: str, tool: str, response: dict) -> None: ...  # first write wins
    def next_attempt(self, name: str) -> int: ...  # returns 0 on first call, then 1, 2, ...
    def add_event(self, type: str, booking_id: str, payload: dict, dedupe_key: str) -> bool: ...
    def pending_events(self, user_id: str | None = None, limit: int = 20) -> list[dict]: ...
    def claim_event(self, eid: str, now: datetime) -> bool: ...  # compare-and-set pending -> processing
    def update_event(self, eid: str, **fields) -> None: ...
    def requeue_stale_events(self, now: datetime, older_than_s: int = 300) -> int: ...


class InMemoryStore:
    def __init__(self, seed: bool = True):
        self._providers, self._slots, self._holds, self._bookings = {}, {}, {}, {}
        self._reviews, self._idem, self._counters, self._events = [], {}, {}, {}
        self._config = None
        if seed:
            self._providers = {p["id"]: p for p in seed_data.providers()}
            self._reviews = seed_data.reviews()
            self._slots = {s["id"]: s for s in seed_data.slots()}
            self._bookings = {b["id"]: b for b in seed_data.bookings()}
            for b in self._bookings.values():
                if b["slot_id"] in self._slots:
                    self._slots[b["slot_id"]]["status"] = "booked"
            self._config = seed_data.config_row()

    def config(self):
        return dict(self._config) if self._config else None

    def areas(self):
        return sorted({p["area"] for p in self._providers.values()})

    def providers(self, service, area):
        return [dict(p) for p in self._providers.values()
                if p["service"] == service and p["area"].lower() == area.lower()]

    def provider(self, pid):
        p = self._providers.get(pid)
        return dict(p) if p else None

    def reviews(self, pid):
        return [dict(r) for r in self._reviews if r["provider_id"] == pid]

    def free_slots(self, pid, start, end):
        rows = [dict(s) for s in self._slots.values()
                if s["provider_id"] == pid and s["status"] == "free" and start <= s["start_at"] < end]
        return sorted(rows, key=lambda s: s["start_at"])

    def slot(self, sid):
        s = self._slots.get(sid)
        return dict(s) if s else None

    def claim_slot(self, sid, frm, to):
        s = self._slots.get(sid)
        if s and s["status"] == frm:
            s["status"] = to
            return True
        return False

    def set_slot_status(self, sid, status):
        if sid in self._slots:
            self._slots[sid]["status"] = status

    def add_hold(self, hold):
        self._holds[hold["id"]] = dict(hold)

    def hold(self, hid):
        h = self._holds.get(hid)
        return dict(h) if h else None

    def update_hold(self, hid, **fields):
        self._holds[hid].update(fields)

    def expire_holds(self, now):
        for h in self._holds.values():
            if h["status"] == "active" and h["expires_at"] <= now:
                h["status"] = "expired"
                self.claim_slot(h["slot_id"], "held", "free")

    def add_booking(self, booking):
        self._bookings[booking["id"]] = dict(booking)

    def booking(self, bid):
        b = self._bookings.get(bid)
        return dict(b) if b else None

    def update_booking(self, bid, **fields):
        self._bookings[bid].update(fields)

    def booking_by(self, hold_id=None, idempotency_key=None):
        for b in self._bookings.values():
            if (hold_id and b.get("hold_id") == hold_id) or (
                idempotency_key and b.get("idempotency_key") == idempotency_key
            ):
                return dict(b)
        return None

    def bookings_for_user(self, user_id):
        return sorted((dict(b) for b in self._bookings.values() if b["user_id"] == user_id),
                      key=lambda b: b["slot_start"])

    def idem(self, key):
        return dict(self._idem[key]) if key in self._idem else None

    def put_idem(self, key, tool, response):
        self._idem.setdefault(key, {"key": key, "tool": tool, "response": dict(response)})

    def next_attempt(self, name):
        n = self._counters.get(name, 0)
        self._counters[name] = n + 1
        return n

    def add_event(self, type, booking_id, payload, dedupe_key):
        if dedupe_key in self._events:
            return False
        self._events[dedupe_key] = {"id": uuid.uuid4().hex, "type": type, "booking_id": booking_id,
                                    "payload": dict(payload), "dedupe_key": dedupe_key, "status": "pending",
                                    "claimed_at": None, "processed_at": None, "run_id": None}
        return True

    def events(self):
        return [dict(e) for e in self._events.values()]

    def _event(self, eid):
        return next((e for e in self._events.values() if e["id"] == eid), None)

    def pending_events(self, user_id=None, limit=20):
        rows = [dict(e) for e in self._events.values() if e["status"] == "pending"
                and (user_id is None or e["payload"].get("user_id") == user_id)]
        return rows[:limit]

    def claim_event(self, eid, now):
        e = self._event(eid)
        if e and e["status"] == "pending":
            e.update(status="processing", claimed_at=now)
            return True
        return False

    def update_event(self, eid, **fields):
        self._event(eid).update(fields)

    def requeue_stale_events(self, now, older_than_s=300):
        n = 0
        for e in self._events.values():
            if e["status"] == "processing" and e["claimed_at"] and (now - e["claimed_at"]).total_seconds() > older_than_s:
                e["status"] = "pending"
                n += 1
        return n
