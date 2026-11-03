"""Wires the app together. STATE_BACKEND=supabase persists everything; memory is for local tests and demos."""
from dataclasses import replace
from datetime import datetime
from functools import lru_cache

from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.orchestrator import Orchestrator
from app.agent.seed import seed_demo
from app.agent.supabase_app_store import SupabaseAppStore
from app.clock import FixedClock, OffsetClock, SystemClock
from app.config import get_settings
from app.llm import OpenAILLM
from app.tools.config import ScenarioConfig
from app.tools.store import InMemoryStore
from app.tools.supabase_store import SupabaseStore


DEFAULT_SIM_NOW = "2026-11-02T10:00:00+05:30"


def make_clock(settings):
    raw = (settings.sim_now or "").strip() or DEFAULT_SIM_NOW  # an empty SIM_NOW means "use the default", not "real time"
    if raw.lower() in ("system", "real", "now"):
        return SystemClock()
    start = datetime.fromisoformat(raw)
    return FixedClock(start) if settings.sim_freeze else OffsetClock(start)


def scenario_config(settings, store) -> ScenarioConfig | None:
    """SCENARIO_MODE=happy: every booking succeeds and quotes match search prices, for a clean first demo.
    Otherwise None: the simulator keeps the assignment's seeded 70/15/15 outcomes (tunable in scenario_config)."""
    if settings.scenario_mode != "happy":
        return None
    return replace(ScenarioConfig.from_row(store.config()), price_drift_prob=0.0, forced_book_outcomes=("success",))


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
    o = Orchestrator(app, store, clock, OpenAILLM(s.openai_api_key, s.openai_model),
                     builder=ContextBuilder(s.context_budget_tokens), tools_config=scenario_config(s, store),
                     price_in_per_1m=s.price_in_per_1m, price_out_per_1m=s.price_out_per_1m, llm_titles=True)
    o.recover()  # finish anything a previous process left half-done
    return o
