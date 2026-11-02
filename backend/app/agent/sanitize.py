"""Scrub tool output before the model sees it. Free text from tools is DATA, never instructions.

This is defence in depth: the real protection is structural (the model has no tool that can
book, cancel, or approve). Regexes only catch the obvious attempts and keep them out of context.
"""
import re

HIDDEN = "[text hidden: contained instructions]"

_INVISIBLE = re.compile(r"[​-‏‪-‮⁠﻿\x00-\x08\x0b-\x1f\x7f]")
_TAGS = re.compile(r"<[^>]{0,40}>")
_INJECTION = [re.compile(p, re.I) for p in (
    r"ignore (all |any )?(the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"\bsystem (instruction|prompt|message|override)",
    r"\b(admin|developer|root) (override|mode|access)\b",
    r"\[\s*(admin|system)",
    r"\byou are now\b",
    r"\b(assistant|ai|model|system)\s*:",
    r"already approved|has approved|skip (the )?(approval|confirmation)|without (asking|confirmation|approval)",
    r"do not (ask|mention|tell|show)",
    r"\bcancel all\b",
    r"\bbook\b.{0,60}\bimmediately\b",
    r"(print|reveal|show|leak|output)\b.{0,50}\b(memor|prompt|instruction|system)",
    r"\bset\b.{0,30}\bbudget\b",
)]


def looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION)


def clean_text(text: str, max_len: int = 300) -> tuple[str, bool]:
    """Returns (safe_text, was_flagged). Flagged text is replaced entirely."""
    t = _TAGS.sub(" ", _INVISIBLE.sub("", text))
    t = re.sub(r"\s+", " ", t).strip()
    if looks_like_injection(t):
        return HIDDEN, True
    return (t[: max_len - 1] + "…" if len(t) > max_len else t), False


def sanitize(obj, flags: list[str] | None = None, max_len: int = 300):
    """Recursively clean every string value. Appends a note to `flags` for each hidden string."""
    if isinstance(obj, str):
        out, hit = clean_text(obj, max_len)
        if hit and flags is not None:
            flags.append(obj[:60])
        return out
    if isinstance(obj, dict):
        return {k: sanitize(v, flags, max_len) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v, flags, max_len) for v in obj]
    return obj
