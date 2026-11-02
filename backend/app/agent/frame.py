"""The request frame: structured facts about the current errand. Code owns 'what's missing'."""
from datetime import datetime

from app.clock import IST

# Changing any of these voids a pending approval and invalidates old search results.
MATERIAL = {"service", "area", "date_start", "date_end", "window_start", "window_end",
            "any_time", "budget_max", "no_budget"}

CLEAR_ALIASES = {
    "date": ["date_start", "date_end"],
    "window": ["window_start", "window_end", "any_time", "time_ambiguous", "time_note"],
    "budget": ["budget_max", "no_budget"],
}


def missing(frame: dict) -> tuple[list[str], list[str]]:
    """(missing, partial). Budget is asked once but 'no limit' is a valid answer."""
    miss, part = [], []
    if not frame.get("service"):
        miss.append("service")
    if not frame.get("area"):
        miss.append("area")
    if not frame.get("date_start"):
        miss.append("date")
    if frame.get("time_ambiguous"):
        part.append("time")  # e.g. "10 to 12" with am/pm unknown
    elif not (frame.get("window_start") or frame.get("window_end") or frame.get("any_time")):
        miss.append("time")
    if frame.get("budget_max") is None and not frame.get("no_budget"):
        miss.append("budget")
    return miss, part


def public(frame: dict) -> dict:
    return {k: v for k, v in frame.items() if not k.startswith("_")}


def apply_updates(frame: dict, updates: dict, clear: list[str]) -> tuple[dict, list[str]]:
    """Merge only what changed. Everything else is preserved (a correction never wipes context)."""
    new = dict(frame)
    for name in clear:
        for k in CLEAR_ALIASES.get(name, [name]):
            new.pop(k, None)
    new.update(updates)
    # mutually exclusive / dependent fields
    if any(k in updates for k in ("window_start", "window_end")):
        new.pop("any_time", None)
        if "time_ambiguous" not in updates:
            new.pop("time_ambiguous", None)
    if updates.get("any_time"):
        new.pop("window_start", None)
        new.pop("window_end", None)
        if "time_ambiguous" not in updates:
            new.pop("time_ambiguous", None)
    if updates.get("budget_max") is not None:
        new.pop("no_budget", None)
    if updates.get("no_budget"):
        new.pop("budget_max", None)
    if new.get("date_start") and (not new.get("date_end") or new["date_end"] < new["date_start"]):
        new["date_end"] = new["date_start"]
    keys = set(frame) | set(new)
    changed = sorted(k for k in keys if not k.startswith("_") and frame.get(k) != new.get(k))
    return new, changed


def slot_in_window(slot: dict, frame: dict) -> bool:
    """slot has ISO 'start'/'end'. The whole slot must fit inside the user's time window."""
    start = datetime.fromisoformat(slot["start"]).astimezone(IST)
    end = datetime.fromisoformat(slot["end"]).astimezone(IST)
    if frame.get("date_start") and start.date().isoformat() < frame["date_start"]:
        return False
    if frame.get("date_end") and start.date().isoformat() > frame["date_end"]:
        return False
    if frame.get("window_start") and start.strftime("%H:%M") < frame["window_start"]:
        return False
    if frame.get("window_end") and end.strftime("%H:%M") > frame["window_end"]:
        return False
    return True
