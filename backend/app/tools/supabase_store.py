from datetime import datetime, timedelta

from app.db import get_db

_DT_FIELDS = ("start_at", "end_at", "expires_at", "slot_start", "created_at", "updated_at", "decided_at",
              "consumed_at", "deadline_at", "started_at", "ended_at", "resolved_at", "locked_until",
              "forgotten_at", "invalidated_at", "read_at", "ts", "claimed_at", "processed_at", "prompted_at", "last_message_at", "deleted_at")


def _read(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {k: datetime.fromisoformat(v) if k in _DT_FIELDS and isinstance(v, str) else v
            for k, v in row.items()}


def _write(fields: dict) -> dict:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in fields.items()}


class SupabaseStore:
    """Same interface as InMemoryStore, backed by the Supabase tables."""

    def __init__(self, db=None):
        self._db = db

    @property
    def db(self):  # resolved per use so each thread gets its own client
        return self._db or get_db()

    def _t(self, name: str):
        return self.db.table(name)

    def _one(self, q) -> dict | None:
        rows = q.limit(1).execute().data
        return _read(rows[0]) if rows else None

    def config(self):
        return self._one(self._t("scenario_config").select("*").eq("scenario", "default"))

    def areas(self):
        return sorted({r["area"] for r in self._t("providers").select("area").execute().data})

    def providers(self, service, area):
        return self._t("providers").select("*").eq("service", service).ilike("area", area).execute().data

    def provider(self, pid):
        return self._one(self._t("providers").select("*").eq("id", pid))

    def reviews(self, pid):
        return self._t("reviews").select("author,text").eq("provider_id", pid).execute().data

    def free_slots(self, pid, start, end):
        rows = (self._t("slots").select("*").eq("provider_id", pid).eq("status", "free")
                .gte("start_at", start.isoformat()).lt("start_at", end.isoformat())
                .order("start_at").execute().data)
        return [_read(r) for r in rows]

    def slot(self, sid):
        return self._one(self._t("slots").select("*").eq("id", sid))

    def claim_slot(self, sid, frm, to):
        rows = self._t("slots").update({"status": to}).eq("id", sid).eq("status", frm).execute().data
        return len(rows) > 0

    def set_slot_status(self, sid, status):
        self._t("slots").update({"status": status}).eq("id", sid).execute()

    def add_hold(self, hold):
        self._t("holds").insert(_write(hold)).execute()

    def hold(self, hid):
        return self._one(self._t("holds").select("*").eq("id", hid))

    def update_hold(self, hid, **fields):
        self._t("holds").update(_write(fields)).eq("id", hid).execute()

    def expire_holds(self, now):
        rows = (self._t("holds").select("id,slot_id").eq("status", "active")
                .lte("expires_at", now.isoformat()).execute().data)
        for r in rows:
            self.update_hold(r["id"], status="expired")
            self.claim_slot(r["slot_id"], "held", "free")

    def add_booking(self, booking):
        self._t("bookings").insert(_write(booking)).execute()

    def booking(self, bid):
        return self._one(self._t("bookings").select("*").eq("id", bid))

    def update_booking(self, bid, **fields):
        self._t("bookings").update(_write(fields)).eq("id", bid).execute()

    def booking_by(self, hold_id=None, idempotency_key=None):
        conds = []
        if hold_id:
            conds.append(f"hold_id.eq.{hold_id}")
        if idempotency_key:
            conds.append(f"idempotency_key.eq.{idempotency_key}")
        if not conds:
            return None
        return self._one(self._t("bookings").select("*").or_(",".join(conds)))

    def bookings_for_user(self, user_id):
        rows = self._t("bookings").select("*").eq("user_id", user_id).order("slot_start").execute().data
        return [_read(r) for r in rows]

    def idem(self, key):
        return self._one(self._t("idempotency_keys").select("*").eq("key", key))

    def put_idem(self, key, tool, response):
        self._t("idempotency_keys").upsert(
            {"key": key, "tool": tool, "response": response}, on_conflict="key", ignore_duplicates=True
        ).execute()

    def next_attempt(self, name):
        key = f"attempt:{name}"
        row = self.idem(key)
        n = row["response"]["n"] if row else 0
        self._t("idempotency_keys").upsert(
            {"key": key, "tool": "counter", "response": {"n": n + 1}}, on_conflict="key"
        ).execute()
        return n

    def add_event(self, type, booking_id, payload, dedupe_key):
        rows = self._t("events").upsert(
            {"type": type, "booking_id": booking_id, "payload": payload, "dedupe_key": dedupe_key},
            on_conflict="dedupe_key", ignore_duplicates=True,
        ).execute().data
        return len(rows) > 0

    def pending_events(self, user_id=None, limit=20):
        q = self._t("events").select("*").eq("status", "pending")
        if user_id:
            q = q.filter("payload->>user_id", "eq", user_id)
        return [_read(r) for r in q.order("created_at").limit(limit).execute().data]

    def claim_event(self, eid, now):
        rows = (self._t("events").update({"status": "processing", "claimed_at": now.isoformat()})
                .eq("id", eid).eq("status", "pending").execute().data)
        return len(rows) > 0  # compare-and-set: only one worker wins

    def update_event(self, eid, **fields):
        self._t("events").update(_write(fields)).eq("id", eid).execute()

    def requeue_stale_events(self, now, older_than_s=300):
        cutoff = (now - timedelta(seconds=older_than_s)).isoformat()
        rows = (self._t("events").update({"status": "pending"}).eq("status", "processing")
                .lt("claimed_at", cutoff).execute().data)
        return len(rows)
