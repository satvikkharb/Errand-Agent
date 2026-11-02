"""App-state storage (sessions, approvals, memories, summaries).

InMemoryAppStore is used by tests and evals. The Supabase-backed version (same methods, the tables
from db/0001_schema.sql) is added with the orchestrator.
"""
import copy
import uuid
from datetime import datetime, timedelta


class ApprovalNotConsumable(Exception):
    """Approval was not in 'approved' state, was altered, or expired."""


class InMemoryAppStore:
    def __init__(self):
        self._sessions, self._approvals, self._memories, self._summaries = {}, {}, {}, []
        self._messages, self._runs, self._spans, self._attempts = {}, {}, {}, {}
        self._notifications, self._locks = [], {}

    # ---- sessions ----
    def create_session(self, user_id: str, state: str = "INTAKE", session_id: str | None = None) -> dict:
        row = {"id": session_id or str(uuid.uuid4()), "user_id": user_id, "state": state, "request_frame": {},
               "active_hold_id": None, "pending_approval_id": None, "version": 0}
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
    def add_summary(self, user_id: str, text: str, session_id: str | None = None, covers_up_to_seq: int = 0) -> None:
        self._summaries.append({"id": str(uuid.uuid4()), "user_id": user_id, "text": text, "invalidated_at": None})

    def invalidate_summaries(self, user_id: str, now: datetime) -> int:
        n = 0
        for s in self._summaries:
            if s["user_id"] == user_id and s["invalidated_at"] is None:
                s["invalidated_at"] = now
                n += 1
        return n

    def valid_summaries(self, user_id: str) -> list[dict]:
        return [dict(s) for s in self._summaries if s["user_id"] == user_id and s["invalidated_at"] is None]

    # ---- messages ----
    def add_message(self, session_id: str, role: str, content: str, run_id: str | None = None) -> dict:
        rows = self._messages.setdefault(session_id, [])
        row = {"id": str(uuid.uuid4()), "session_id": session_id, "seq": len(rows) + 1,
               "role": role, "content": content, "run_id": run_id}
        rows.append(row)
        return dict(row)

    def messages(self, session_id: str) -> list[dict]:
        return [dict(m) for m in self._messages.get(session_id, [])]

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
                         event_id: str | None = None) -> dict:
        row = {"id": str(uuid.uuid4()), "user_id": user_id, "session_id": session_id, "event_id": event_id,
               "body": body, "created_at": now, "read_at": None}
        self._notifications.append(row)
        return dict(row)

    def notifications(self, user_id: str) -> list[dict]:
        return [dict(n) for n in self._notifications if n["user_id"] == user_id]

    # ---- per-user lock (expires on its own so a crash can never wedge a user) ----
    def acquire_lock(self, user_id: str, now: datetime, ttl_s: int = 60) -> bool:
        until = self._locks.get(user_id)
        if until and until > now:
            return False
        self._locks[user_id] = now + timedelta(seconds=ttl_s)
        return True

    def release_lock(self, user_id: str) -> None:
        self._locks.pop(user_id, None)
