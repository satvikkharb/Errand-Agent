"""Proactive follow-up with the real model. A provider cancels; nobody asks; the agent notices and tells you.

Run from backend/:  PYTHONPATH=. STATE_BACKEND=supabase uv run python scripts/live_proactive.py
(then scripts/reset_db.sh if you used Supabase)
"""
from dataclasses import replace
from datetime import datetime

from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.orchestrator import Orchestrator
from app.agent.seed import seed_demo
from app.agent.supabase_app_store import SupabaseAppStore
from app.clock import IST, FixedClock
from app.config import get_settings
from app.llm import OpenAILLM
from app.tools.config import ScenarioConfig
from app.tools.sim import provider_cancels
from app.tools.store import InMemoryStore
from app.tools.supabase_store import SupabaseStore


def trace(o, run_id):
    for s in o.app.spans(run_id):
        if s["kind"] == "event":
            print(f"    · event {s['name']}  {s.get('output') or s.get('input')}")
        elif s["kind"] == "tool_call":
            print(f"    · tool  {s['name']}  {str(s['output'])[:100] if s['output'] is not None else s['error']}")
        elif s["kind"] == "model_call":
            print(f"    · model in={s['tokens_in']} out={s['tokens_out']} -> {[t['name'] for t in s['output']['tool_calls']] or 'text'}")
        elif s["kind"] == "state_transition":
            print(f"    · state {s['name']}")


def main():
    s = get_settings()
    clock = FixedClock(datetime(2026, 11, 2, 10, 0, tzinfo=IST))
    if s.state_backend == "supabase":
        store, app = SupabaseStore(), SupabaseAppStore()
    else:
        store, app = InMemoryStore(), InMemoryAppStore()
        seed_demo(app, clock.now())
    cfg = replace(ScenarioConfig.from_row(store.config()), price_drift_prob=0.0, forced_book_outcomes=("success",))
    o = Orchestrator(app, store, clock, OpenAILLM(s.openai_api_key, s.openai_model), tools_config=cfg,
                     builder=ContextBuilder(s.context_budget_tokens), sleep=lambda x: None)
    print(f"backend={s.state_backend}\n")
    print("WORLD   > CoolBreeze AC Care cancels the user's AC servicing (booking bk_004, Thu 5 Nov 14:00)")
    provider_cancels(store, "bk_004")
    print("          (no user message is sent. The agent's tick runs on its own)\n")
    res = o.tick(user_id="user_demo")[0]
    note = app.notifications("user_demo", unread_only=True)[-1]
    print(f"AGENT   > [notification, {note['urgency']}] {note['body']}")
    trace(o, res["run_id"])
    sid = note["session_id"]
    print("\nUSER opens the notification and says:  \"Yes, book that replacement.\"")
    r = o.handle_message("user_demo", "Yes, book that replacement.", sid)
    print(f"AGENT   > {r.reply}   [state={r.state}]")
    trace(o, r.run_id)
    if r.approval_card:
        print("   CARD  ", " | ".join(r.approval_card["lines"]), "  [Approve] [Cancel]")
        print("\nUSER taps [Approve]")
        out = o.approve("user_demo", r.approval_card["approval_id"])
        print(f"AGENT   > {out.reply}")
        trace(o, out.run_id)
    mine = store.bookings_for_user("user_demo")
    print("\nbookings:", [(b["id"], b["provider_id"], b["status"]) for b in mine if b["service"] == "ac_servicing"])


if __name__ == "__main__":
    main()
