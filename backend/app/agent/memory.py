"""Persistent memory rules, in code. The model proposes; this decides and writes.

  explicit   the user said it           -> active at once
  inferred   we noticed a pattern       -> pending_confirmation, NEVER shown to the model, asked about once
  forgotten  tombstone                  -> value gone, key kept so it is not re-inferred

Forgetting is thorough: the stored value, any superseded versions, the chat history that still quotes it,
summaries that mention it, and open requests that were pre-filled from it.
"""
import re
from collections import Counter
from datetime import datetime
from typing import Any

from app.agent.sanitize import clean_text
from app.clock import IST

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

MEMORY_KEYS = {  # key -> value kind
    "preferred_area": "str", "earliest_time": "time", "latest_time": "time", "preferred_time_of_day": "str",
    "avoid_provider": "str", "preferred_plumber": "str", "preferred_electrician": "str",
    "preferred_ac_servicing": "str", "default_budget_plumber": "int", "default_budget_electrician": "int",
    "default_budget_ac": "int", "weekend_ok": "bool", "home_type": "str", "language": "str",
}
BUDGET_KEY = {"plumber": "default_budget_plumber", "electrician": "default_budget_electrician",
              "ac_servicing": "default_budget_ac"}
TIME_OF_DAY = {"morning": ("06:00", "12:00"), "afternoon": ("12:00", "17:00"), "evening": ("17:00", "20:00")}
# which request-frame fields a saved preference can pre-fill, and the keys that feed them
FRAME_FIELDS = {"area": ["area"], "budget": ["budget_max"], "time": ["window_start", "window_end", "time_ambiguous", "time_note"]}
KEY_TO_FRAME = {"preferred_area": "area", "preferred_time_of_day": "time",
                **{k: "budget" for k in BUDGET_KEY.values()}}
FRAME_NAME_OF_FIELD = {f: name for name, fs in FRAME_FIELDS.items() for f in fs} | {"no_budget": "budget", "any_time": "time"}
PROMPTABLE = {"preferred_time_of_day", "preferred_plumber", "preferred_electrician", "preferred_ac_servicing"}


def coerce(kind: str, v: Any) -> Any:
    """Validate/normalise a value for a memory key. None = invalid."""
    try:
        if kind == "int":
            return int(v) if not isinstance(v, bool) and int(v) > 0 else None
        if kind == "bool":
            return v if isinstance(v, bool) else {"true": True, "false": False}[str(v).lower()]
        if kind == "time":
            return str(v) if _TIME.match(str(v)) else None
        text, flagged = clean_text(str(v), 80)
        return None if flagged or not text else text
    except (ValueError, KeyError):
        return None


def bucket(dt: datetime) -> str:
    h = dt.astimezone(IST).hour
    return "morning" if h < 12 else "afternoon" if h < 16 else "evening"


class MemoryService:
    def __init__(self, app, clock):
        self.app, self.clock = app, clock

    # ------------------------------------------------------------ explicit writes
    def write_explicit(self, user_id: str, key: str, value: Any, source_message_id: str | None = None) -> str:
        """The user stated a preference. Returns saved | updated | unchanged | unknown_key | bad_value."""
        kind = MEMORY_KEYS.get(key)
        if not kind:
            return "unknown_key"
        value = coerce(kind, value)
        if value is None:
            return "bad_value"
        now = self.clock.now()
        live = [m for m in self.app.memories(user_id) if m["key"] == key and m["superseded_by"] is None
                and m["status"] in ("active", "pending_confirmation")]
        if any(m["status"] == "active" and m["value"] == value for m in live):
            return "unchanged"
        new = self.app.add_memory({"user_id": user_id, "key": key, "value": value, "kind": "explicit",
                                   "status": "active", "confidence": 1.0, "source_message_id": source_message_id,
                                   "created_at": now, "updated_at": now})
        for m in live:  # a correction supersedes, an explicit word beats a pending guess
            self.app.update_memory(m["id"], superseded_by=new["id"], updated_at=now)
        return "updated" if any(m["status"] == "active" for m in live) else "saved"

    # ------------------------------------------------------------ inference
    def infer(self, user_id: str, bookings: list[dict], provider_name) -> list[dict]:
        """Candidates from real behaviour. Never asks twice, never contradicts the user."""
        done = [b for b in bookings if b["status"] in ("confirmed", "completed")]
        known = {m["key"] for m in self.app.memories(user_id) if m["superseded_by"] is None}  # incl. tombstones
        out: list[dict] = []
        if len(done) >= 3 and "preferred_time_of_day" not in known:
            top, n = Counter(bucket(b["slot_start"]) for b in done).most_common(1)[0]
            if n >= 3 and n / len(done) >= 0.75:
                out.append({"key": "preferred_time_of_day", "value": top, "confidence": round(n / len(done), 2),
                            "text": f"I noticed you've booked {top} slots {n} times. "
                                    f"Want me to remember that you prefer {top}s?"})
        for service in sorted({b["service"] for b in done}):
            key = f"preferred_{service}"
            if key not in MEMORY_KEYS or key in known:
                continue
            counts = Counter(b["provider_id"] for b in done if b["service"] == service)
            pid, n = counts.most_common(1)[0]
            total = sum(counts.values())
            if n >= 2 and n / total >= 0.66:
                name = provider_name(pid) or pid
                label = service.replace("_", " ")
                out.append({"key": key, "value": pid, "confidence": round(n / total, 2),
                            "text": f"I noticed you've booked {name} {n} times. "
                                    f"Want me to remember {name} as your preferred {label} provider?"})
        return out

    def after_booking(self, user_id: str, session_id: str, bookings: list[dict], provider_name) -> dict | None:
        """After a verified booking: store new candidates, then ask about at most one. Returns the prompt."""
        now = self.clock.now()
        for c in self.infer(user_id, bookings, provider_name):
            self.app.add_memory({"user_id": user_id, "key": c["key"], "value": c["value"], "kind": "inferred",
                                 "status": "pending_confirmation", "confidence": c["confidence"],
                                 "created_at": now, "updated_at": now})
        session = self.app.session(session_id)
        if session["request_frame"].get("_confirm_memory"):
            return None  # one question at a time
        pending = [m for m in self.app.memories(user_id)
                   if m["kind"] == "inferred" and m["status"] == "pending_confirmation"
                   and m["superseded_by"] is None and not m.get("prompted_at") and m["key"] in PROMPTABLE]
        if not pending:
            return None
        m = max(pending, key=lambda x: x["confidence"] or 0)
        self.app.update_memory(m["id"], prompted_at=now)
        frame = session["request_frame"]
        frame["_confirm_memory"] = m["id"]  # the very next user message may answer it
        self.app.update_session(session_id, request_frame=frame)
        return {"memory_id": m["id"], "key": m["key"], "value": m["value"],
                "text": self._ask(m, provider_name), "buttons": ["Yes, remember", "No"]}

    @staticmethod
    def _ask(m: dict, provider_name) -> str:
        if m["key"] == "preferred_time_of_day":
            return f"I think you prefer {m['value']} slots. Want me to remember that?"
        label = m["key"].removeprefix("preferred_").replace("_", " ")
        return f"I think {provider_name(m['value']) or m['value']} is your preferred {label} provider. Want me to remember that?"

    # ------------------------------------------------------------ confirm / reject / correct
    def _mine(self, user_id: str, memory_id: str) -> dict | None:
        return next((m for m in self.app.memories(user_id) if m["id"] == memory_id), None)

    def confirm(self, user_id: str, memory_id: str) -> dict | None:
        m = self._mine(user_id, memory_id)
        if not m or m["status"] != "pending_confirmation" or m["superseded_by"]:
            return None
        self.app.update_memory(memory_id, status="active", updated_at=self.clock.now())
        return self._mine(user_id, memory_id)

    def reject(self, user_id: str, memory_id: str) -> bool:
        m = self._mine(user_id, memory_id)
        if not m or m["status"] != "pending_confirmation":
            return False
        now = self.clock.now()  # tombstone: "no" is remembered, so it is not asked again
        self.app.update_memory(memory_id, value=None, status="forgotten", forgotten_at=now, updated_at=now)
        return True

    def correct(self, user_id: str, memory_id: str, value: Any) -> str:
        m = self._mine(user_id, memory_id)
        if not m or m["status"] == "forgotten" or m["superseded_by"]:
            return "not_found"
        return self.write_explicit(user_id, m["key"], value)

    # ------------------------------------------------------------ forget
    def forget(self, user_id: str, *, key: str | None = None, memory_id: str | None = None,
               everything: bool = False) -> dict | None:
        """Tombstone, and chase the fact out of history, summaries and pre-filled requests. None = nothing to forget."""
        mems = [m for m in self.app.memories(user_id) if m["status"] != "forgotten"]
        if memory_id:
            target = next((m for m in mems if m["id"] == memory_id), None)
            key = target["key"] if target else None
            if not target:
                return None
        rows = [m for m in mems if everything or m["key"] == key]  # includes superseded versions
        if not rows:
            return None
        now = self.clock.now()
        needles = sorted({str(m["value"]) for m in rows if isinstance(m["value"], str) and len(m["value"]) >= 4})
        for m in rows:
            self.app.update_memory(m["id"], value=None, status="forgotten", forgotten_at=now, updated_at=now)
        redacted = sum(self.app.redact_messages(user_id, n) for n in needles)
        summaries = self.app.invalidate_summaries(user_id, now)  # regenerated later, from the redacted chat
        names = {KEY_TO_FRAME[m["key"]] for m in rows if m["key"] in KEY_TO_FRAME}
        scrubbed = self._scrub_frames(user_id, names, {m["id"] for m in rows}, needles)
        return {"count": len(rows), "messages_redacted": redacted, "summaries_dropped": summaries,
                "requests_scrubbed": scrubbed}

    def _scrub_frames(self, user_id: str, names: set[str], memory_ids: set[str], needles: list[str]) -> int:
        """Open requests lose what was borrowed from a forgotten fact, and an area that matches it however it got there
        (the model may have copied it into the request itself)."""
        n = 0
        lowered = {x.lower() for x in needles}
        for s in self.app.sessions_for_user(user_id):
            f = s["request_frame"]
            hit = [x for x in f.get("_from_memory", []) if x in names]
            if "area" in names and "area" not in hit and str(f.get("area", "")).lower() in lowered:
                hit.append("area")
            drop_prompt = f.get("_confirm_memory") in memory_ids
            if not hit and not drop_prompt:
                continue
            for name in hit:
                for field in FRAME_FIELDS[name]:
                    f.pop(field, None)
                if name in f.get("_from_memory", []):
                    f["_from_memory"].remove(name)
            if hit:
                for k in ("_search", "_suggestion"):
                    f.pop(k, None)
            if not f.get("_from_memory"):
                f.pop("_from_memory", None)
            if drop_prompt:
                f.pop("_confirm_memory", None)
            self.app.update_session(s["id"], request_frame=f)
            n += bool(hit)
        return n

    # ------------------------------------------------------------ inspect
    def list_for_user(self, user_id: str) -> list[dict]:
        """Everything the user can see: current explicit facts and pending guesses. No tombstones, no history."""
        return sorted(({k: m.get(k) for k in ("id", "key", "value", "kind", "status", "confidence", "updated_at")}
                       for m in self.app.memories(user_id)
                       if m["status"] != "forgotten" and m["superseded_by"] is None), key=lambda m: m["key"])

    def prefs(self, user_id: str) -> dict:
        """Confirmed, current preferences as {key: value}. The only memory the gateway and search may use."""
        return {m["key"]: m["value"] for m in self.app.active_memories(user_id)}
