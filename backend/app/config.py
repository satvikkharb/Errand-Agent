from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""  # direct Postgres connection string (percent-encode special characters in the password)
    db_access: str = "postgres"  # postgres = direct pooled connection (fast) | rest = Supabase REST API
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    # LangSmith tracing (off unless LANGSMITH_TRACING=true AND an API key is set). See docs/langsmith.md.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "errand-agent"
    langsmith_endpoint: str = "https://api.smith.langchain.com"  # EU accounts: https://eu.api.smith.langchain.com
    langsmith_workspace_id: str = ""  # only for organization-scoped keys
    langsmith_send_prompts: bool = True  # false: send timings, tokens and tool names, but not prompt/reply text
    state_backend: str = "memory"  # memory | supabase
    cron_secret: str = ""  # Authorization: Bearer <this> on /internal/tick (Vercel Cron sends it)
    dev_endpoints: bool = False  # enables /dev/simulate/*
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
