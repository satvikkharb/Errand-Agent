"""The provider table the user picks from: every match with ALL its available slots, as data (not prose).

The frontend renders it as rows of `Provider | Rating | slots` with a bubble per slot; tapping one calls
POST /conversations/{id}/select, which goes straight to the approval card without asking the model.
Built from the saved search, so it is always exactly what the booking rules will accept.
"""
from datetime import datetime, timedelta

from app.agent.timefmt import date_text, human_day, human_range, when, window_text


def build_options(frame: dict, pending_approval: dict | None = None) -> dict | None:
    s = frame.get("_search")
    if not s or not s.get("slots"):
        return None
    providers = []
    for rank, (pid, ids) in enumerate(s["slots"].items(), 1):
        slots = []
        for sid in ids:
            start = datetime.fromisoformat(s["times"][sid])
            end = datetime.fromisoformat(s["ends"][sid]) if s.get("ends", {}).get(sid) else start + timedelta(hours=2)
            slots.append({"id": sid, "label": when(start.isoformat(), end.isoformat()),  # "Mon 9 Nov, 2-4 pm"
                          "day": human_day(start), "time": human_range(start, end),  # the same, split for grouping
                          "start": start.isoformat(), "end": end.isoformat(),
                          "in_window": s.get("in_window", {}).get(sid, True)})
        providers.append({"id": pid, "name": s["names"][pid], "rating": s["ratings"][pid], "price": s["prices"][pid],
                          "visit_fee": s.get("visit_fees", {}).get(pid), "rank": rank, "recommended": rank == 1,
                          "total_slots": s.get("totals", {}).get(pid, len(slots)), "slots": slots})
    selected = None
    if pending_approval and pending_approval["status"] == "pending" and pending_approval["action"] == "book":
        p = pending_approval["payload"]
        selected = {"provider_id": p["provider_id"], "slot_id": p["slot_id"]}  # the frontend highlights this bubble
    what = " ".join(x for x in ((frame.get("service") or "").replace("_", " ").capitalize(),
                                f"in {frame['area']}" if frame.get("area") else "") if x)
    when_ = ", ".join(x for x in (date_text(frame.get("date_start"), frame.get("date_end")),
                                  window_text(frame.get("window_start"), frame.get("window_end"), frame.get("any_time"))) if x)
    return {
        "kind": "provider_slots", "search_id": s.get("search_id"),
        "title": " \u00b7 ".join(x for x in (what, when_) if x),  # "Plumber in Koramangala · Mon 9 Nov to Sun 15 Nov, 12 pm to 6 pm"
        "request": {"service": frame.get("service"), "area": frame.get("area"),
                    "date_text": date_text(frame.get("date_start"), frame.get("date_end")),
                    "time_text": window_text(frame.get("window_start"), frame.get("window_end"), frame.get("any_time")),
                    "budget": frame.get("budget_max")},
        "relaxed": s.get("relaxed", []),  # e.g. ["time_window"]: nothing fit their window, these are the slots that exist
        "matches": providers, "selected": selected}
