"""Conversation titles and the history list.

A title exists from the first real message (so the sidebar is never blank), is upgraded by a small model call
when enabled, and is never overwritten once the user names it themselves.

  extractive  cut from the user's first real message      (instant, free, always works)
  llm         a short title written by the model          (one cheap call after the first exchange)
  system      set by the app (e.g. a provider-cancellation follow-up)
  user        the user renamed it: final
"""
import re
import time

from app.agent.sanitize import clean_text

FALLBACK = "New conversation"
MAX_TITLE = 80
EXTRACT_LEN = 48
_GREETING = re.compile(r"^(hi|hello|hey|hiya|good (morning|afternoon|evening))\b[\s,.!-]*", re.I)
_LEAD = re.compile(r"^(please|pls|can you|could you|would you|will you|i want to|i would like to|i'd like to|i need to|"
                   r"i need|i want|help me)\b[\s,.-]*", re.I)

TITLE_SYSTEM = ("Write a title of 3 to 6 words for this conversation between a user and an errand assistant. "
                "Name the errand (service, and place or day if given). No quotes, no trailing punctuation. "
                "Output only the title.")


def extractive_title(text: str) -> str | None:
    """A readable title from the user's own words, or None if the message says too little (e.g. 'hi')."""
    t, flagged = clean_text(text or "", 200)
    if flagged:
        return None
    for _ in range(3):  # strip greetings and filler, however they stack ("hi, can you please ...")
        new = _LEAD.sub("", _GREETING.sub("", t)).strip()
        if new == t:
            break
        t = new
    t = t.strip(" ,.-!?—")
    if len(t.split()) < 3:
        return None
    t = t[0].upper() + t[1:]
    if len(t) <= EXTRACT_LEN:
        return t.rstrip(".?!")
    cut = t[:EXTRACT_LEN]
    if t[EXTRACT_LEN] != " ":  # only back up to a word boundary when the cut landed mid-word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.-") + "…"


def clean_title(text: str) -> str | None:
    """Validate a title someone (user or model) supplied. None = unusable."""
    t = " ".join((text or "").split()).strip("\"'“”‘’ ").rstrip(".")
    if not t or len(t) > MAX_TITLE:
        return None
    safe, flagged = clean_text(t, MAX_TITLE)
    return None if flagged or not safe else safe


class TitleService:
    def __init__(self, app, llm, clock, use_llm: bool = False):
        self.app, self.llm, self.clock, self.use_llm = app, llm, clock, use_llm

    def ensure_initial(self, session_id: str, message: str) -> None:
        """Called each turn until a good title exists: a first message of 'hi' must not become the title."""
        s = self.app.session(session_id)
        if s.get("title_source") in ("user", "llm", "system") or s.get("title"):
            return
        title = extractive_title(message)
        if title:
            self.app.set_title(session_id, title, "extractive")

    def upgrade(self, session_id: str, tracer=None) -> None:
        """After the first exchange, replace the cut-out title with a model-written one. Never raises."""
        if not self.use_llm:
            return
        try:
            s = self.app.session(session_id)
            msgs = [m for m in self.app.messages(session_id) if m["role"] in ("user", "assistant")]
            # only improve a title cut from a real message: a bare "hello" has nothing worth naming yet
            if s.get("title_source") != "extractive" or not (2 <= len(msgs) <= 6) or msgs[-1]["role"] != "assistant":
                return
            convo = "\n".join(f"{m['role']}: {m['content'][:300]}" for m in msgs)
            t0 = time.perf_counter()
            r = self.llm.complete([{"role": "system", "content": TITLE_SYSTEM}, {"role": "user", "content": convo}], [])
            if tracer:
                tracer.span("model_call", f"{r.model}:title", input={"messages": len(msgs)},
                            output={"content": (r.content or "")[:100]}, latency_ms=int((time.perf_counter() - t0) * 1000),
                            tokens_in=r.usage.prompt_tokens, tokens_out=r.usage.completion_tokens,
                            ls_messages=[{"role": "system", "content": TITLE_SYSTEM}, {"role": "user", "content": convo}],
                            ls_output={"content": r.content})
            title = clean_title(r.content or "")
            if title:
                self.app.set_title(session_id, title, "llm")
        except Exception:
            return  # the cut-out title stays: a title must never break a chat turn


class HistoryService:
    """What the sidebar and the conversation view need. Every query is scoped to one user."""

    def __init__(self, app, clock):
        self.app, self.clock = app, clock

    @staticmethod
    def _shape(c: dict) -> dict:
        return {**c, "title": c.get("title") or FALLBACK}

    def list(self, user_id: str, limit: int = 30, before=None) -> list[dict]:
        return [self._shape(c) for c in self.app.list_conversations(user_id, limit, before)]

    def rename(self, user_id: str, session_id: str, title: str) -> str:
        """ok | not_found | bad_title. A user's title is final: nothing overwrites it afterwards."""
        s = self.app.session(session_id)
        if not s or s["user_id"] != user_id or s.get("deleted_at"):
            return "not_found"
        clean = clean_title(title)
        if not clean:
            return "bad_title"
        self.app.set_title(session_id, clean, "user")
        return "ok"
