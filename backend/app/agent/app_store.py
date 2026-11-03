"""App-state storage (sessions, approvals, memories, summaries).

InMemoryAppStore is used by tests and evals. The Supabase-backed version (same methods, the tables
from db/0001_schema.sql) is added with the orchestrator.
"""
import copy
import re
import uuid
from datetime import datetime, timedelta, timezone


class ApprovalNotConsumable(Exception):
    """Approval was not in 'approved' state, was altered, or expired."""


class InMemoryAppStore:
    def __init__(self):
        self._sessions, self._approvals, self._memories, self._summaries = {}, {}, {}, []
        self._messages, self._runs, self._spans, self._attempts = {}, {}, {}, {}
        self._notifications, self._locks = [], {}
        self._last_stamp = datetime(2000, 1, 1, tzinfo=timezone.utc)

    # ---- sessions ----
    def _stamp(self) -> datetime:
        """Strictly increasing timestamps, so ordering never depends on clock resolution."""
        now = datetime.now(timezone.utc)
        self._last_stamp = max(now, self._last_stamp + timedelta(microseconds=1))
        return self._last_stamp

    def create_session(self, user_id: str, state: str = "INTAKE", session_id: str | None = None) -> dict:
        row = {"id": session_id or str(uuid.uuid4()), "user_id": user_id, "state": state, "request_frame": {},
               "active_hold_id": None, "pending_approval_id": None, "version": 0, "title": None,
               "title_source": None, "deleted_at": None, "created_at": self._stamp()}
        self._sessions[row["id"]] = row
        return copy.deepcopy(row)

    def session(self, sid: str) -> dict | None:
        s = self._sessions.get(sid)
        return copy.deepcopy(s) if s else None

    def update_session(self, sid: str, **fields) -> None:
        self._sessions[sid].update(copy.deepcopy(fields))
        self._sessions[sid]["version"] += 1

    # ---- approvals ----
    def add_approval(self, row: dict) -> dict:
        row = {"id": str(uuid.uuid4()), "decided_at": None, "consumed_at": None, **copy.deepcopy(row)}
        self._approvals[row["id"]] = row
        return copy.deepcopy(row)

    def approval(self, aid: str) -> dict | None:
        a = self._approvals.get(aid)
        return copy.deepcopy(a) if a else None

    def update_approval(self, aid: str, **fields) -> None:
        self._approvals[aid].update(copy.deepcopy(fields))

    # ---- memories ----
    def add_memory(self, row: dict) -> dict:
        row = {"id": str(uuid.uuid4()), "superseded_by": None, "forgotten_at": None, **copy.deepcopy(row)}
        self._memories[row["id"]] = row
        return copy.deepcopy(row)

    def memories(self, user_id: str) -> list[dict]:
        return [copy.deepcopy(m) for m in self._memories.values() if m["user_id"] == user_id]

    def update_memory(self, mid: str, **fields) -> None:
        self._memories[mid].update(copy.deepcopy(fields))

    def active_memories(self, user_id: str) -> list[dict]:
        """What may reach the model: confirmed (active), current (not superseded), not forgotten."""
        return [m for m in self.memories(user_id)
                if m["status"] == "active" and m["superseded_by"] is None and m["value"] is not None]

    # ---- summaries ----
    def add_summary(self, user_id: str, text: str, session_id: str | None = None, covers_up_to_seq: int = 0) -> dict:
        row = {"id": str(uuid.uuid4()), "user_id": user_id, "session_id": session_id, "text": text,
               "covers_up_to_seq": covers_up_to_seq, "invalidated_at": None}
        self._summaries.append(row)
        return dict(row)

    def invalidate_summary(self, summary_id: str, now: datetime) -> None:
        for s in self._summaries:
            if s["id"] == summary_id:
                s["invalidated_at"] = s["invalidated_at"] or now

    def invalidate_summaries(self, user_id: str, now: datetime) -> int:
        n = 0
        for s in self._summaries:
            if s["user_id"] == user_id and s["invalidated_at"] is None:
                s["invalidated_at"] = now
                n += 1
        return n

    def valid_summaries(self, user_id: str) -> list[dict]:
        return [dict(s) for s in self._summaries if s["user_id"] == user_id and s["invalidated_at"] is None]

    # ---- conversation history (the sidebar) ----
    def list_conversations(self, user_id: str, limit: int = 30, before: datetime | None = None) -> list[dict]:
        out = []
        for s in self._sessions.values():
            msgs = self._messages.get(s["id"]) or []
            if s["user_id"] != user_id or s.get("deleted_at") or not msgs:
                continue
            last = msgs[-1]
            if before is not None and not last["created_at"] < before:
                continue
            a = self._approvals.get(s["pending_approval_id"]) if s["pending_approval_id"] else None
            out.append({"id": s["id"], "title": s.get("title"), "title_source": s.get("title_source"),
                        "state": s["state"], "created_at": s["created_at"], "last_message_at": last["created_at"],
                        "message_count": len(msgs), "preview": last["content"][:140],
                        "has_pending_approval": bool(a and a["status"] == "pending")})
        out.sort(key=lambda r: r["last_message_at"], reverse=True)
        return out[:limit]

    def set_title(self, session_id: str, title: str | None, source: str | None) -> None:
        self._sessions[session_id].update(title=title, title_source=source)

    def soft_delete_session(self, session_id: str, now: datetime) -> None:
        self._sessions[session_id]["deleted_at"] = now

    def sessions_for_user(self, user_id: str) -> list[dict]:
        return [copy.deepcopy(s) for s in self._sessions.values() if s["user_id"] == user_id]

    # ---- messages ----
    def add_message(self, session_id: str, role: str, content: str, run_id: str | None = None) -> dict:
        rows = self._messages.setdefault(session_id, [])
        row = {"id": str(uuid.uuid4()), "session_id": session_id, "seq": len(rows) + 1,
               "role": role, "content": content, "run_id": run_id, "created_at": self._stamp()}
        rows.append(row)
        return dict(row)

    def messages(self, session_id: str) -> list[dict]:
        return [dict(m) for m in self._messages.get(session_id, [])]

    def redact_messages(self, user_id: str, needle: str) -> int:
        """Replace a forgotten fact wherever the user's own chat history still contains it."""
        if len(needle.strip()) < 4:
            return 0
        pat, n = re.compile(re.escape(needle), re.I), 0
        for sess in self._sessions.values():  # conversation titles can quote the fact too
            if sess["user_id"] == user_id and sess.get("title"):
                sess["title"] = pat.sub("[forgotten]", sess["title"])
        for sid, rows in self._messages.items():
            if self._sessions[sid]["user_id"] != user_id:
                continue
            for m in rows:
                new = pat.sub("[forgotten]", m["content"])
                if new != m["content"]:
                    m["content"], n = new, n + 1
        return n

    # ---- runs and trace spans ----
    def create_run(self, session_id: str, trigger: str, deadline: datetime, now: datetime) -> dict:
        row = {"id": str(uuid.uuid4()), "session_id": session_id, "trigger": trigger, "status": "running",
               "turns": 0, "tool_calls": 0, "deadline_at": deadline, "exit_reason": None,
               "final_message": None, "started_at": now, "ended_at": None}
        self._runs[row["id"]] = row
        return dict(row)

    def update_run(self, run_id: str, **fields) -> None:
        self._runs[run_id].update(fields)

    def run(self, run_id: str) -> dict | None:
        r = self._runs.get(run_id)
        return dict(r) if r else None

    def close_stale_runs(self, now: datetime) -> int:
        """Runs a crashed process left 'running' past their deadline."""
        n = 0
        for r in self._runs.values():
            if r["status"] == "running" and r["deadline_at"] < now:
                r.update(status="failed", exit_reason="error", ended_at=now,
                         final_message=r["final_message"] or "interrupted")
                n += 1
        return n

    def add_span(self, run_id: str, span: dict) -> None:
        rows = self._spans.setdefault(run_id, [])
        rows.append({"id": str(uuid.uuid4()), "run_id": run_id, "seq": len(rows), **copy.deepcopy(span)})

    def spans(self, run_id: str) -> list[dict]:
        return copy.deepcopy(self._spans.get(run_id, []))

    # ---- booking attempts (write-ahead log) ----
    def begin_attempt(self, approval_id: str, payload_hash: str, hold_id: str | None, now: datetime) -> dict:
        """Atomically consume an approved approval and record the attempt. One approval => one attempt."""
        a = self._approvals.get(approval_id)
        if (not a or a["status"] != "approved" or a["payload_hash"] != payload_hash
                or a["expires_at"] <= now or approval_id in {x["approval_id"] for x in self._attempts.values()}):
            raise ApprovalNotConsumable(approval_id)
        a.update(status="consumed", consumed_at=now)
        row = {"id": str(uuid.uuid4()), "approval_id": approval_id, "action": a["action"],
               "idempotency_key": f"idem_{approval_id}", "status": "started", "hold_id": hold_id,
               "booking_id": None, "tool_response": None, "retry_count": 0, "started_at": now,
               "resolved_at": None}
        self._attempts[row["id"]] = row
        return dict(row)

    def attempt(self, attempt_id: str) -> dict | None:
        r = self._attempts.get(attempt_id)
        return dict(r) if r else None

    def update_attempt(self, attempt_id: str, **fields) -> None:
        self._attempts[attempt_id].update(copy.deepcopy(fields))

    def unresolved_attempts(self, user_id: str | None = None) -> list[dict]:
        out = []
        for r in self._attempts.values():
            if r["status"] not in ("started", "unknown"):
                continue
            a = self._approvals[r["approval_id"]]
            if user_id is None or self._sessions[a["session_id"]]["user_id"] == user_id:
                out.append(dict(r))
        return out

    # ---- notifications (the local "push": shown next time the user opens the chat) ----
    def add_notification(self, user_id: str, session_id: str | None, body: str, now: datetime,
                         event_id: str | None = None, urgency: str = "normal") -> dict:
        row = {"id": str(uuid.uuid4()), "user_id": user_id, "session_id": session_id, "event_id": event_id,
               "body": body, "created_at": now, "read_at": None, "urgency": urgency}
        self._notifications.append(row)
        return dict(row)

    def notifications(self, user_id: str, unread_only: bool = False) -> list[dict]:
        return [dict(n) for n in self._notifications
                if n["user_id"] == user_id and (not unread_only or n["read_at"] is None)]

    def notification_for_event(self, event_id: str) -> dict | None:
        return next((dict(n) for n in self._notifications if n["event_id"] == event_id), None)

    def mark_notification_read(self, notification_id: str, user_id: str, now: datetime) -> bool:
        for n in self._notifications:
            if n["id"] == notification_id and n["user_id"] == user_id:
                n["read_at"] = n["read_at"] or now
                return True
        return False

    # ---- per-user lock (expires on its own so a crash can never wedge a user) ----
    def acquire_lock(self, user_id: str, now: datetime, ttl_s: int = 60) -> bool:
        until = self._locks.get(user_id)
        if until and until > now:
            return False
        self._locks[user_id] = now + timedelta(seconds=ttl_s)
        return True

    def release_lock(self, user_id: str) -> None:
        self._locks.pop(user_id, None)
