"""Wires the app together. STATE_BACKEND=supabase persists everything; memory is for local tests and demos."""
from datetime import datetime
from functools import lru_cache

from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.orchestrator import Orchestrator
from app.agent.seed import seed_demo
from app.agent.supabase_app_store import SupabaseAppStore
from app.clock import FixedClock, SystemClock
from app.config import get_settings
from app.llm import OpenAILLM
from app.tools.store import InMemoryStore
from app.tools.supabase_store import SupabaseStore


@lru_cache
def get_orchestrator() -> Orchestrator | None:
    s = get_settings()
    if not s.openai_api_key:
        return None
    clock = FixedClock(datetime.fromisoformat(s.sim_now)) if s.sim_now else SystemClock()
    if s.state_backend == "supabase":  # the DB is seeded by db/0002_seed.sql
        app, store = SupabaseAppStore(), SupabaseStore()
    else:
        app, store = InMemoryAppStore(), InMemoryStore()
        seed_demo(app, clock.now())
    o = Orchestrator(app, store, clock, OpenAILLM(s.openai_api_key, s.openai_model),
                     builder=ContextBuilder(s.context_budget_tokens),
                     price_in_per_1m=s.price_in_per_1m, price_out_per_1m=s.price_out_per_1m)
    o.recover()  # finish anything a previous process left half-done
    return o
