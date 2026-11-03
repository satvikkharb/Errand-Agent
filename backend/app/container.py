"""Wires the app together. STATE_BACKEND=supabase persists everything; memory is for local tests and demos."""
from datetime import datetime
from functools import lru_cache

from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.orchestrator import Orchestrator
from app.agent.seed import seed_demo
from app.agent.supabase_app_store import SupabaseAppStore
from app.agent.telemetry import Telemetry
from app.clock import FixedClock, OffsetClock, SystemClock
from app.config import get_settings
from app.llm import OpenAILLM
from app.tools.store import InMemoryStore
from app.tools.supabase_store import SupabaseStore


DEFAULT_SIM_NOW = "2026-11-02T10:00:00+05:30"


def make_clock(settings):
    raw = (settings.sim_now or "").strip() or DEFAULT_SIM_NOW  # an empty SIM_NOW means "use the default", not "real time"
    if raw.lower() in ("system", "real", "now"):
        return SystemClock()
    start = datetime.fromisoformat(raw)
    if settings.sim_freeze:
        return FixedClock(start)
    anchor = datetime.fromisoformat(settings.sim_anchor) if (settings.sim_anchor or "").strip() else None
    return OffsetClock(start, anchor=anchor, loop_hours=settings.sim_loop_hours or None)


@lru_cache
def get_orchestrator() -> Orchestrator | None:
    s = get_settings()
    if not s.openai_api_key:
        return None
    clock = make_clock(s)
    if s.state_backend == "supabase":  # the DB is seeded by db/0002_seed.sql
        app, store = SupabaseAppStore(), SupabaseStore()
    else:
        app, store = InMemoryAppStore(), InMemoryStore()
        seed_demo(app, clock.now())
    llm = OpenAILLM(s.openai_api_key, s.openai_model)
    o = Orchestrator(app, store, clock, llm, memory_llm=llm,
                     builder=ContextBuilder(s.context_budget_tokens),
                     price_in_per_1m=s.price_in_per_1m, price_out_per_1m=s.price_out_per_1m, llm_titles=True,
                     telemetry=Telemetry.from_settings(s))
    o.recover()  # finish anything a previous process left half-done
    return o
