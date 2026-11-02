from typing import Any, Protocol


class Tracer(Protocol):
    def span(self, kind: str, name: str, input: Any = None, output: Any = None,
             latency_ms: int | None = None, error: str | None = None, **extra) -> None: ...


class NullTracer:
    def span(self, kind, name, input=None, output=None, latency_ms=None, error=None, **extra):
        pass


class InMemoryTracer:
    """Collects spans in order. The Supabase-backed tracer (trace_spans table) comes with the orchestrator."""

    def __init__(self):
        self.spans: list[dict] = []

    def span(self, kind, name, input=None, output=None, latency_ms=None, error=None, **extra):
        self.spans.append({"seq": len(self.spans), "kind": kind, "name": name, "input": input,
                           "output": output, "latency_ms": latency_ms, "error": error, **extra})

    def of(self, kind: str) -> list[dict]:
        return [s for s in self.spans if s["kind"] == kind]
