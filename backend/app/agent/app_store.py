"""App-state storage (sessions, approvals, memories, summaries).

InMemoryAppStore is used by tests and evals. The Supabase-backed version (same methods, the tables
from db/0001_schema.sql) is added with the orchestrator.
"""
import copy
import uuid
from datetime import datetime


class InMemoryAppStore:
    def __init__(self):
        self._sessions, self._approvals, self._memories, self._summaries = {}, {}, {}, []

    # ---- sessions ----
    def create_session(self, user_id: str, state: str = "INTAKE") -> dict:
        row = {"id": str(uuid.uuid4()), "user_id": user_id, "state": state, "request_frame": {},
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
    def add_summary(self, user_id: str, text: str) -> None:
        self._summaries.append({"user_id": user_id, "text": text, "invalidated_at": None})

    def invalidate_summaries(self, user_id: str, now: datetime) -> int:
        n = 0
        for s in self._summaries:
            if s["user_id"] == user_id and s["invalidated_at"] is None:
                s["invalidated_at"] = now
                n += 1
        return n

    def valid_summaries(self, user_id: str) -> list[dict]:
        return [dict(s) for s in self._summaries if s["user_id"] == user_id and s["invalidated_at"] is None]
