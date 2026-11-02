"""The model behind a tiny interface: real OpenAI in the app, scripted in tests and evals."""
import copy
import itertools
from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.agent.tokens import messages_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON string, exactly as the model produced it


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = "scripted"


class LLM(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse: ...


class OpenAILLM:
    def __init__(self, api_key: str, model: str, timeout_s: float = 30.0):
        from openai import OpenAI  # imported lazily so tests never need the network client

        self.client = OpenAI(api_key=api_key, timeout=timeout_s, max_retries=2)
        self.model = model

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        r = self.client.chat.completions.create(
            model=self.model, messages=messages, tools=tools, tool_choice="auto", parallel_tool_calls=False)
        msg = r.choices[0].message
        return LLMResponse(
            content=msg.content,
            tool_calls=[ToolCall(t.id, t.function.name, t.function.arguments) for t in (msg.tool_calls or [])],
            usage=Usage(r.usage.prompt_tokens, r.usage.completion_tokens) if r.usage else Usage(),
            model=r.model)


_ids = itertools.count(1)


def say(text: str) -> LLMResponse:
    return LLMResponse(content=text)


def call(name: str, **args) -> LLMResponse:
    import json
    return LLMResponse(tool_calls=[ToolCall(f"call_{next(_ids)}", name, json.dumps(args))])


class ScriptedLLM:
    """Plays back a fixed script. A step is an LLMResponse, an Exception to raise, or a
    function(messages) -> LLMResponse (to read ids out of the conversation). Records every
    request so tests can assert exactly what context the model was given."""

    def __init__(self, steps: list[LLMResponse | Exception | Callable]):
        self.steps = list(steps)
        self.calls: list[dict] = []

    def complete(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        if not self.steps:
            raise AssertionError("ScriptedLLM ran out of steps")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        r = step(messages) if callable(step) else step
        if not r.usage.prompt_tokens:
            r.usage = Usage(messages_tokens(messages), 20)
        return r
