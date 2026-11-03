"""LangSmith tracing: every orchestrator run is one trace, every span we record is a child run.

  chat / approve / reject / select_slot / open_proposal / event      -> the root run (one trace)
    context          the context that was built (token count, which memories, history dropped)   chain
    <model>          each model call: the exact prompt sent, the reply, tokens, estimated cost    llm
    <tool name>      each tool call the model made (args in, sanitized result out)              tool
    exec.<tool>      each side-effect call the executor made (book, status check, retries)     tool
    state / approval / event   workflow steps, approvals created/approved/invalidated            chain

The conversation id is sent as `thread_id`, so LangSmith's Threads view shows each chat as one thread.
Tracing is strictly best-effort: nothing in here can raise into a request, and with no API key it is off.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

RUN_TYPE = {"model_call": "llm", "tool_call": "tool"}  # every other span kind is a "chain" step
LABEL = {"state_transition": "state"}  # shorter names in the LangSmith tree
MAX_TEXT = 8000  # per string sent to LangSmith
SUCCESS_OUTCOMES = {"booked", "cancelled", "rescheduled"}


def _clip(value: Any, limit: int = MAX_TEXT) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… [{len(value) - limit} more chars]"
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v, limit) for v in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Telemetry:
    """Holds the LangSmith client and settings. Create runs with start()."""

    def __init__(self, client: Any, project: str = "errand-agent", send_prompts: bool = True, flush_wait_s: float = 0.0):
        self.client, self.project, self.send_prompts, self.flush_wait_s = client, project, send_prompts, flush_wait_s
        self._project_id: str | None = None
        self._threads: list[threading.Thread] = []

    def background(self, fn) -> None:
        """Run network work (flush, scores) off the request path. A slow or dead LangSmith must not slow a reply."""
        def run():
            try:
                fn()
            except Exception:
                log.warning("LangSmith: background send failed", exc_info=True)
        t = threading.Thread(target=run, daemon=True, name="langsmith-flush")
        self._threads = [x for x in self._threads if x.is_alive()] + [t]
        t.start()

    def drain(self, timeout: float = 10.0) -> None:
        """Wait for background sends (tests, and a clean shutdown)."""
        for t in list(self._threads):
            t.join(timeout)

    def project_id(self) -> str | None:
        """LangSmith wants the project's id next to a score. It exists once the first run has been ingested."""
        if self._project_id is None:
            try:
                self._project_id = str(self.client.read_project(project_name=self.project).id)
            except Exception:
                return None
        return self._project_id

    @classmethod
    def from_settings(cls, s) -> "Telemetry | None":
        """None unless LANGSMITH_TRACING is on and an API key is set."""
        if not (s.langsmith_tracing and s.langsmith_api_key):
            return None
        from langsmith import Client  # imported lazily: the app runs fine without the package configured

        client = Client(api_url=s.langsmith_endpoint, api_key=s.langsmith_api_key,
                        workspace_id=s.langsmith_workspace_id or None,
                        tracing_error_callback=lambda e: log.warning("LangSmith tracing error: %s", e))
        return cls(client, s.langsmith_project, s.langsmith_send_prompts, s.langsmith_flush_wait_s)

    def wrap(self, app_tracer, *, name: str, run_id: str, session_id: str, user_id: str | None, inputs: dict):
        """The tracer the rest of the code uses: it writes spans to our database AND to LangSmith."""
        return CompositeTracer(app_tracer, RunTrace(self, name, run_id, session_id, user_id, inputs))


class RunTrace:
    """One LangSmith trace (root run + children) for one orchestrator run."""

    def __init__(self, tel: Telemetry, name: str, run_id: str, session_id: str, user_id: str | None, inputs: dict):
        self.tel, self.root, self.notes, self.tags = tel, None, {}, {name}
        self.injections = 0
        try:
            from langsmith.run_trees import RunTree

            self.root = RunTree(
                name=name, run_type="chain", inputs=_clip(inputs), project_name=tel.project, client=tel.client,
                extra={"metadata": {"session_id": session_id, "thread_id": session_id, "conversation_id": session_id,
                                    "user_id": user_id, "run_id": run_id, "trigger": name}},
                tags=sorted(self.tags))
            self.root.post()  # visible while it is still running
        except Exception:
            log.warning("LangSmith: could not start a trace", exc_info=True)
            self.root = None

    # ---- children
    def span(self, kind: str, name: str, input=None, output=None, latency_ms=None, error=None, **extra) -> None:
        if self.root is None:
            return
        try:
            end = _now()
            start = end - timedelta(milliseconds=latency_ms or 0)
            if extra.get("injection_flags"):
                self.injections += len(extra["injection_flags"])
            run_type = RUN_TYPE.get(kind, "chain")
            label = name if kind in RUN_TYPE else f"{LABEL.get(kind, kind)}: {name}"
            inputs, outputs, meta = self._payload(kind, name, input, output, extra)
            child = self.root.create_child(name=label, run_type=run_type, inputs=inputs, start_time=start,
                                           tags=[kind], extra={"metadata": meta})
            child.end(outputs=outputs, error=error, end_time=end)
            child.post()
        except Exception:
            log.warning("LangSmith: could not record a step", exc_info=True)

    def _payload(self, kind: str, name: str, input, output, extra: dict):
        meta: dict = {"kind": kind}
        if kind == "model_call":
            model, _, purpose = name.partition(":")  # "gpt-4.1-mini-2025-04-14:summarizer"
            meta.update({"ls_provider": "openai", "ls_model_name": model, "purpose": purpose or "agent",
                         "estimated_cost_usd": extra.get("cost_usd")})
            messages = extra.get("ls_messages")
            inputs = {"messages": _clip(messages)} if (self.tel.send_prompts and messages) else {"summary": _clip(input)}
            reply = extra.get("ls_output") or output or {}
            content = reply.get("content") if self.tel.send_prompts else "[hidden]"
            outputs = {"choices": [{"message": {"role": "assistant", "content": _clip(content),
                                                 "tool_calls": _clip(reply.get("tool_calls") or [])}}]}
            tin, tout = extra.get("tokens_in"), extra.get("tokens_out")
            if tin is not None:
                outputs["usage_metadata"] = {"input_tokens": tin, "output_tokens": tout or 0, "total_tokens": tin + (tout or 0)}
            return inputs, outputs, meta
        if extra.get("injection_flags"):
            meta["injection_attempts_hidden"] = len(extra["injection_flags"])
        return (_clip(input) if isinstance(input, dict) else {"input": _clip(input)},
                _clip(output) if isinstance(output, dict) else {"output": _clip(output)}, meta)

    # ---- the run as a whole
    def note(self, **metadata) -> None:
        self.notes.update({k: v for k, v in metadata.items() if v is not None})

    def finish(self, outputs: dict, error: str | None = None, metadata: dict | None = None) -> None:
        if self.root is None:
            return
        try:
            tags = set(self.tags)
            outcome = self.notes.get("outcome")
            if self.injections:
                tags.add("injection-blocked")
            if outcome:
                tags.add(f"outcome:{outcome}")
            exit_reason = (outputs or {}).get("exit_reason")
            if exit_reason and exit_reason not in ("done", None):
                tags.add(f"exit:{exit_reason}")
            self.root.add_tags(sorted(tags))
            self.root.add_metadata({**self.notes, **(metadata or {}), "injection_attempts_hidden": self.injections})
            self.root.end(outputs=_clip(outputs), error=error)
            self.root.patch()
            self.tel.background(lambda: self._send(outcome))
            if self.tel.flush_wait_s:  # serverless: the process can freeze the moment we reply, so let the send finish first
                self.tel.drain(self.tel.flush_wait_s)
        except Exception:
            log.warning("LangSmith: could not finish a trace", exc_info=True)


    def _send(self, outcome) -> None:
        self.tel.client.flush()
        if outcome:  # a score LangSmith can chart over time
            ok = outcome in SUCCESS_OUTCOMES
            pid = self.tel.project_id()
            self.tel.client.create_feedback(self.root.id, key="booking_verified" if ok else "action_failed",
                                            score=1.0 if ok else 0.0, comment=str(outcome),
                                            **({"session_id": pid} if pid else {}))
            self.tel.client.flush()


class CompositeTracer:
    """Writes each span to our database tracer and to LangSmith. The rest of the code sees one tracer."""

    def __init__(self, primary, run_trace: RunTrace):
        self.primary, self.ls = primary, run_trace

    def span(self, kind, name, input=None, output=None, latency_ms=None, error=None, **extra):
        self.primary.span(kind, name, input=input, output=output, latency_ms=latency_ms, error=error,
                          **{k: v for k, v in extra.items() if not k.startswith("ls_")})  # prompts stay out of our DB
        self.ls.span(kind, name, input, output, latency_ms, error, **extra)

    def note(self, **metadata):
        self.ls.note(**metadata)

    def finish(self, outputs: dict, error: str | None = None, metadata: dict | None = None):
        self.ls.finish(outputs, error, metadata)
