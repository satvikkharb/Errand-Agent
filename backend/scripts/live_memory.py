"""Persistent memory with the real model: state preferences, see them reused, forget one, prove it is gone.

Run from backend/:  PYTHONPATH=. uv run python scripts/live_memory.py
"""
import json
from dataclasses import replace
from datetime import datetime

from app.agent.app_store import InMemoryAppStore
from app.agent.orchestrator import Orchestrator
from app.clock import IST, FixedClock
from app.config import get_settings
from app.llm import OpenAILLM
from app.tools.config import ScenarioConfig
from app.tools.store import InMemoryStore

USER = "live_mem"


class Recorder:
    """Wraps the real model and keeps every request, so we can inspect exactly what it was shown."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def complete(self, messages, tools):
        self.calls.append([dict(m) for m in messages])
        return self.inner.complete(messages, tools)


def show(o, r):
    for s in o.app.spans(r.run_id):
        if s["kind"] == "tool_call":
            print(f"    · tool  {s['name']}  {json.dumps(s['input'])[:90]}  ->  {str(s['output'])[:90]}")


def main():
    s = get_settings()
    clock = FixedClock(datetime(2026, 11, 2, 10, 0, tzinfo=IST))
    store, app = InMemoryStore(), InMemoryAppStore()
    llm = Recorder(OpenAILLM(s.openai_api_key, s.openai_model))
    cfg = replace(ScenarioConfig.from_row(store.config()), price_drift_prob=0.0, forced_book_outcomes=("success",))
    o = Orchestrator(app, store, clock, llm, tools_config=cfg, sleep=lambda x: None)
    sid = None

    def say(msg):
        nonlocal sid
        print(f"USER  > {msg}")
        r = o.handle_message(USER, msg, sid)
        sid = r.session_id
        print(f"AGENT > {r.reply}")
        show(o, r)
        return r

    def stored():
        return [(m["key"], m["value"], m["kind"], m["status"]) for m in o.memory.list_for_user(USER)]

    say("Hi! For future reference I live in Indiranagar, and I never want anyone coming before 10 am.")
    print("   stored:", stored(), "\n")
    r = say("Find me a plumber for a leaking tap tomorrow. Budget is 1500.")
    print("   frame :", {k: v for k, v in app.session(sid)["request_frame"].items() if not k.startswith("_")}, "\n")
    say("Go with your top pick.")
    print()
    say("What do you remember about me?")
    print()
    say("Please forget my area.")
    print("   stored:", stored())
    forgot = not any(k == "preferred_area" for k, *_ in stored())
    print(f"   forget succeeded: {forgot}")
    before = len(llm.calls)
    say("Thanks. Find me an electrician for tomorrow afternoon, up to 1200.")
    seen = "\n".join(m["content"] or "" for call in llm.calls[before:] for m in call)
    if not forgot:
        print("\n(the forget did not go through this time, so the area is legitimately still stored: no leak check)")
        return
    print(f"\nWas 'Indiranagar' anywhere in what the model saw after forgetting?  {'YES (leak!)' if 'indiranagar' in seen.lower() else 'no'}")
    for call in llm.calls[before:]:
        for m in call:
            for line in (m["content"] or "").splitlines():
                if "indiranagar" in line.lower():
                    print(f"   leak in [{m['role']}]: {line[:150]}")
    print("Chat history now:", [m["content"][:60] for m in app.messages(sid) if m["role"] == "user"][:2])


if __name__ == "__main__":
    main()
