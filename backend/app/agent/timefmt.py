"""How times and prices are said to a person: "2 pm", "2-4 pm", "Wed 4 Nov". Never "14:00"."""
from datetime import datetime

from app.clock import IST

GST_RATE = 0.18


def _clock(h: int, m: int) -> str:
    return f"{h % 12 or 12}:{m:02d}" if m else f"{h % 12 or 12}"


def human_time(dt: datetime) -> str:
    """2 pm, 2:30 pm."""
    dt = dt.astimezone(IST)
    return f"{_clock(dt.hour, dt.minute)} {'am' if dt.hour < 12 else 'pm'}"


def human_range(start: datetime, end: datetime) -> str:
    """2-4 pm (one am/pm when both ends share it), 11 am-1 pm otherwise."""
    s, e = start.astimezone(IST), end.astimezone(IST)
    if (s.hour < 12) == (e.hour < 12):
        return f"{_clock(s.hour, s.minute)}\u2013{_clock(e.hour, e.minute)} {'am' if e.hour < 12 else 'pm'}"
    return f"{human_time(s)}\u2013{human_time(e)}"


def human_day(dt: datetime) -> str:
    dt = dt.astimezone(IST)
    return f"{dt.strftime('%a')} {dt.day} {dt.strftime('%b')}"


def when(start_iso: str, end_iso: str | None = None) -> str:
    """Wed 4 Nov, 2-4 pm  (or just the start time when the end is unknown)."""
    s = datetime.fromisoformat(start_iso).astimezone(IST)
    if end_iso:
        return f"{human_day(s)}, {human_range(s, datetime.fromisoformat(end_iso))}"
    return f"{human_day(s)}, {human_time(s)}"


def hhmm_text(hhmm: str) -> str:
    """'14:00' -> '2 pm'."""
    h, m = (int(x) for x in hhmm.split(":"))
    return f"{_clock(h, m)} {'am' if h < 12 else 'pm'}"


def window_text(start: str | None, end: str | None, any_time: bool = False) -> str | None:
    if end in ("23:59", "24:00"):  # "until midnight" is just "after X"
        end = None
    if start and end:
        return f"{hhmm_text(start)} to {hhmm_text(end)}"
    if start:
        return f"after {hhmm_text(start)}"
    if end:
        return f"before {hhmm_text(end)}"
    return "any time" if any_time else None


def date_text(start: str | None, end: str | None) -> str | None:
    if not start:
        return None
    d0 = datetime.fromisoformat(start).replace(tzinfo=IST)
    if not end or end == start:
        return human_day(d0)
    return f"{human_day(d0)} to {human_day(datetime.fromisoformat(end).replace(tzinfo=IST))}"


def price_breakdown(base: int, total: int) -> list[dict]:
    """What the checkout shows. Chat only ever quotes the service price (base); taxes and fees live here.
    Whatever the quote adds on top of the listed price is split into GST and a service fee."""
    items = [{"label": "Service", "amount": base}]
    fees = max(0, total - base)
    if fees:
        gst = min(fees, round(base * GST_RATE))
        items.append({"label": "GST (18%)" if gst == round(base * GST_RATE) else "GST", "amount": gst})
        if fees - gst:
            items.append({"label": "Service fee", "amount": fees - gst})
    return items
