"""Supabase-backed app state. Same interface as InMemoryAppStore (see tests/test_app_store_contract.py).

Atomic things live in Postgres functions (db/0004_app_store.sql): begin_attempt, acquire_user_lock,
unresolved_attempts, close_stale_runs. Everything else is plain row access under the per-user lock.

Trace spans are buffered per run and written in one insert when the run closes (or when read), so a
chat turn costs one extra round trip, not one per span.
"""
import json
import re
import uuid
from datetime import datetime, timedelta

from postgrest.exceptions import APIError

from app.agent.app_store import ApprovalNotConsumable
from app.db import get_db
from app.tools.supabase_store import _read, _write

_SPAN_COLS = ("kind", "name", "input", "output", "latency_ms", "tokens_in", "tokens_out", "cost_usd", "error")


def _uuid_ok(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def _jsonable(x):
    return json.loads(json.dumps(x, default=str))


class SupabaseAppStore:
    def __init__(self, db=None):
        self._db = db
        self._buf: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}

    @property
    def db(self):  # resolved per use so each thread gets its own client
        return self._db or get_db()

    def _t(self, name: str):
        return self.db.table(name)

    def _one(self, q) -> dict | None:
        rows = q.limit(1).execute().data
        return _read(rows[0]) if rows else None

    def _get(self, table: str, id_: str) -> dict | None:
        return self._one(self._t(table).select("*").eq("id", id_)) if _uuid_ok(id_) else None

    def ensure_user(self, user_id: str) -> None:
        self._t("users").upsert({"id": user_id, "name": user_id}, on_conflict="id", ignore_duplicates=True).execute()

    # ---- sessions ----
    def create_session(self, user_id, state="INTAKE", session_id=None):
        self.ensure_user(user_id)
        row = {"user_id": user_id, "state": state, "request_frame": {}}
        if session_id:
            row["id"] = session_id
        return _read(self._t("sessions").insert(row).execute().data[0])

    def session(self, sid):
        return self._get("sessions", sid)

    def update_session(self, sid, **fields):
        self._t("sessions").update(_write(fields)).eq("id", sid).execute()  # trigger bumps version

    # ---- approvals ----
    def add_approval(self, row):
        return _read(self._t("approvals").insert(_write(row)).execute().data[0])

    def approval(self, aid):
        return self._get("approvals", aid)

    def update_approval(self, aid, **fields):
        self._t("approvals").update(_write(fields)).eq("id", aid).execute()

    # ---- memories ----
    def add_memory(self, row):
        return _read(self._t("memories").insert(_write(row)).execute().data[0])

    def memories(self, user_id):
        return [_read(r) for r in self._t("memories").select("*").eq("user_id", user_id).execute().data]

    def update_memory(self, mid, **fields):
        self._t("memories").update(_write(fields)).eq("id", mid).execute()

    def active_memories(self, user_id):
        rows = (self._t("memories").select("*").eq("user_id", user_id).eq("status", "active")
                .is_("superseded_by", "null").execute().data)
        return [_read(r) for r in rows if r["value"] is not None]

    # ---- summaries (stored per session, read per user) ----
    def _session_ids(self, user_id: str) -> list[str]:
        return [r["id"] for r in self._t("sessions").select("id").eq("user_id", user_id).execute().data]

    def add_summary(self, user_id, text, session_id=None, covers_up_to_seq=0):
        if session_id is None:
            ids = self._session_ids(user_id)
            if not ids:
                raise ValueError("a summary needs a session")
            session_id = ids[-1]
        row = self._t("conversation_summaries").insert(
            {"session_id": session_id, "text": text, "covers_up_to_seq": covers_up_to_seq}).execute().data[0]
        return {**_read(row), "user_id": user_id}

    def invalidate_summary(self, summary_id, now):
        if _uuid_ok(summary_id):
            self._t("conversation_summaries").update({"invalidated_at": now.isoformat()}).eq("id", summary_id) \
                .is_("invalidated_at", "null").execute()

    def sessions_for_user(self, user_id):
        return [_read(r) for r in self._t("sessions").select("*").eq("user_id", user_id).execute().data]

    def invalidate_summaries(self, user_id, now):
        ids = self._session_ids(user_id)
        if not ids:
            return 0
        rows = (self._t("conversation_summaries").update({"invalidated_at": now.isoformat()})
                .in_("session_id", ids).is_("invalidated_at", "null").execute().data)
        return len(rows)

    def valid_summaries(self, user_id):
        ids = self._session_ids(user_id)
        if not ids:
            return []
        rows = (self._t("conversation_summaries").select("*").in_("session_id", ids)
                .is_("invalidated_at", "null").order("created_at").execute().data)
        return [{**_read(r), "user_id": user_id} for r in rows]

    # ---- messages ----
    def add_message(self, session_id, role, content, run_id=None):
        last = self._one(self._t("messages").select("seq").eq("session_id", session_id).order("seq", desc=True))
        row = {"session_id": session_id, "seq": (last["seq"] if last else 0) + 1, "role": role,
               "content": content, "run_id": run_id}
        return _read(self._t("messages").insert(row).execute().data[0])

    def messages(self, session_id):
        if not _uuid_ok(session_id):
            return []
        rows = self._t("messages").select("*").eq("session_id", session_id).order("seq").execute().data
        return [_read(r) for r in rows]

    def redact_messages(self, user_id, needle):
        """Replace a forgotten fact wherever the user's own chat history still contains it."""
        ids = self._session_ids(user_id)
        if not ids or len(needle.strip()) < 4:
            return 0
        pat, n = re.compile(re.escape(needle), re.I), 0
        q = self._t("messages").select("id,content").in_("session_id", ids)
        if not any(c in needle for c in "%_\\*,()"):  # safe to let the database narrow it down
            q = q.ilike("content", f"%{needle}%")
        for r in q.execute().data:
            new = pat.sub("[forgotten]", r["content"])
            if new != r["content"]:
                self._t("messages").update({"content": new}).eq("id", r["id"]).execute()
                n += 1
        return n

    # ---- runs and trace spans ----
    def create_run(self, session_id, trigger, deadline, now):
        row = {"session_id": session_id, "trigger": trigger, "deadline_at": deadline, "started_at": now}
        return _read(self._t("runs").insert(_write(row)).execute().data[0])

    def update_run(self, run_id, **fields):
        self._t("runs").update(_write(fields)).eq("id", run_id).execute()
        if "ended_at" in fields:  # the run is over: write its trace
            self._flush(run_id)

    def run(self, run_id):
        return self._get("runs", run_id)

    def add_span(self, run_id, span):
        self._buf.setdefault(run_id, []).append(span)

    def _flush(self, run_id: str) -> None:
        spans = self._buf.pop(run_id, [])
        if not spans:
            return
        base = self._seq.get(run_id)
        if base is None:
            last = self._one(self._t("trace_spans").select("seq").eq("run_id", run_id).order("seq", desc=True))
            base = last["seq"] + 1 if last else 0
        rows = []
        for i, sp in enumerate(spans):
            extra = {k: v for k, v in sp.items() if k not in _SPAN_COLS}
            rows.append({"run_id": run_id, "seq": base + i, **{k: _jsonable(sp.get(k)) for k in _SPAN_COLS},
                         "extra": _jsonable(extra) if extra else None})
        self._t("trace_spans").insert(rows).execute()
        self._seq[run_id] = base + len(spans)

    def spans(self, run_id):
        if not _uuid_ok(run_id):
            return []
        self._flush(run_id)
        rows = self._t("trace_spans").select("*").eq("run_id", run_id).order("seq").execute().data
        out = []
        for r in rows:
            extra = r.pop("extra", None) or {}
            out.append({**_read(r), **extra})
        return out

    def close_stale_runs(self, now):
        return self.db.rpc("close_stale_runs", {"p_now": now.isoformat()}).execute().data

    # ---- booking attempts (write-ahead log) ----
    def begin_attempt(self, approval_id, payload_hash, hold_id, now):
        try:
            data = self.db.rpc("begin_attempt", {"p_approval_id": approval_id, "p_hash": payload_hash,
                                                 "p_hold_id": hold_id, "p_now": now.isoformat()}).execute().data
        except APIError as e:
            if "approval_not_consumable" in str(e) or getattr(e, "code", "") in ("23505", "22P02"):
                raise ApprovalNotConsumable(approval_id) from e
            raise
        return _read(data)

    def attempt(self, attempt_id):
        return self._get("booking_attempts", attempt_id)

    def update_attempt(self, attempt_id, **fields):
        self._t("booking_attempts").update(_write(fields)).eq("id", attempt_id).execute()

    def unresolved_attempts(self, user_id=None):
        rows = self.db.rpc("unresolved_attempts", {"p_user": user_id}).execute().data
        return [_read(r) for r in rows]

    # ---- notifications ----
    def add_notification(self, user_id, session_id, body, now, event_id=None, urgency="normal"):
        row = {"user_id": user_id, "session_id": session_id, "event_id": event_id, "body": body,
               "created_at": now, "urgency": urgency}
        return _read(self._t("notifications").insert(_write(row)).execute().data[0])

    def notifications(self, user_id, unread_only=False):
        q = self._t("notifications").select("*").eq("user_id", user_id)
        if unread_only:
            q = q.is_("read_at", "null")
        return [_read(r) for r in q.order("created_at").execute().data]

    def notification_for_event(self, event_id):
        return self._one(self._t("notifications").select("*").eq("event_id", event_id))

    def mark_notification_read(self, notification_id, user_id, now):
        if not _uuid_ok(notification_id):
            return False
        rows = (self._t("notifications").update({"read_at": now.isoformat()}).eq("id", notification_id)
                .eq("user_id", user_id).execute().data)
        return len(rows) > 0

    # ---- per-user lock ----
    def acquire_lock(self, user_id, now, ttl_s=60):
        return bool(self.db.rpc("acquire_user_lock", {"p_user": user_id, "p_now": now.isoformat(),
                                                      "p_until": (now + timedelta(seconds=ttl_s)).isoformat()}).execute().data)

    def release_lock(self, user_id):
        self._t("users").update({"locked_until": None}).eq("id", user_id).execute()
