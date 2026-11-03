from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    state_backend: str = "memory"  # memory | supabase
    cron_secret: str = ""  # Authorization: Bearer <this> on /internal/tick (Vercel Cron sends it)
    dev_endpoints: bool = False  # enables /dev/simulate/*
    scenario_mode: str = "random"  # random: the 70/15/15 booking outcomes | happy: every booking succeeds, no price change
    # "Now" for the simulation. The data is seeded for 2 Nov 2026, so that is "today" by default, running in real time
    # from 10:00 IST. SIM_NOW=system uses the machine clock; SIM_FREEZE=true stops the clock (holds never expire).
    sim_now: str = "2026-11-02T10:00:00+05:30"
    sim_freeze: bool = False
    context_budget_tokens: int = 6000
    price_in_per_1m: float = 0.40  # USD per 1M tokens; estimate, set to your model's real price
    price_out_per_1m: float = 1.60


@lru_cache
def get_settings() -> Settings:
    return Settings()
