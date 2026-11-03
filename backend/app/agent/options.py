"""The provider table the user picks from: every match with ALL its available slots, as data (not prose).

The frontend renders it as rows of `Provider | Rating | slots` with a bubble per slot; tapping one calls
POST /conversations/{id}/select, which goes straight to the approval card without asking the model.
Built from the saved search, so it is always exactly what the booking rules will accept.
"""
from datetime import datetime, timedelta

from app.agent.timefmt import date_text, human_day, human_range, when, window_text


def _reschedule_view(frame: dict) -> dict | None:
    return frame.get("_avail_view") if frame.get("_options_kind") == "reschedule_slots" else None


def options_id(frame: dict) -> str | None:
    """Identifies the table currently on screen (a search or a reschedule view), so a NEW one can be told from a repeat."""
    v = _reschedule_view(frame)
    return v["view_id"] if v else (frame.get("_search") or {}).get("search_id")


def _reschedule_options(frame: dict, v: dict, pending: dict | None) -> dict:
    """The free times of the provider the user is moving a booking with: one row, a bubble per time."""
    slots = []
    for s in v["slots"]:
        start = datetime.fromisoformat(s["start"])
        end = datetime.fromisoformat(s["end"]) if s.get("end") else start + timedelta(hours=2)
        slots.append({"id": s["id"], "label": when(start.isoformat(), end.isoformat()),
                      "day": human_day(start), "time": human_range(start, end),
                      "start": start.isoformat(), "end": end.isoformat(), "in_window": True})
    selected = None
    if pending and pending["status"] == "pending" and pending["action"] == "reschedule" \
            and pending["payload"].get("booking_id") == v["booking_id"]:
        selected = {"provider_id": v["provider_id"], "slot_id": pending["payload"]["to_slot_id"]}
    dates = date_text(v["date_start"], v["date_end"])
    return {
        "kind": "reschedule_slots", "view_id": v["view_id"], "search_id": v["view_id"],
        "title": f"New times for {v['provider_name']} \u00b7 {dates}",
        "booking": {"id": v["booking_id"], "provider_id": v["provider_id"], "provider_name": v["provider_name"],
                    "service": v["service"], "current": v["current"]},  # "Tue 3 Nov, 6-8 pm": where it is now
        "request": {"service": v["service"], "area": None, "date_text": dates, "time_text": None, "budget": None},
        "relaxed": [],
        "matches": [{"id": v["provider_id"], "name": v["provider_name"], "rating": v.get("rating"), "price": v["price"],
                     "visit_fee": None, "rank": 1, "recommended": False, "total_slots": len(slots), "slots": slots}],
        "selected": selected}


def build_options(frame: dict, pending_approval: dict | None = None) -> dict | None:
    view = _reschedule_view(frame)
    if view:
        return _reschedule_options(frame, view, pending_approval)
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
