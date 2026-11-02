"""Deterministic token estimate (~4 chars/token). Good enough to enforce a budget offline.

A real tokenizer (tiktoken) would need to download vocab files, which breaks offline evals.
"""
import json
import math


def count_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def message_tokens(m: dict) -> int:
    n = 4 + count_tokens(m.get("content") or "")
    if m.get("tool_calls"):
        n += count_tokens(json.dumps(m["tool_calls"]))
    return n


def messages_tokens(ms: list[dict]) -> int:
    return sum(message_tokens(m) for m in ms)
