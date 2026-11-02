from dataclasses import dataclass
from typing import Any

from app.agent.trace import Tracer
from app.clock import Clock
from app.tools.provider_tools import ProviderTools


@dataclass
class Deps:
    app: Any  # InMemoryAppStore / Supabase app store
    store: Any  # provider-world Store (trusted, code-only reads)
    tools: ProviderTools  # the assignment's tools, bound to one user
    clock: Clock
    tracer: Tracer
    approval_ttl_min: int = 10
