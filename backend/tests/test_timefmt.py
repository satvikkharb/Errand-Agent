"""Times and prices are said the way a person says them."""
import re
from datetime import datetime

import pytest

from app.agent.timefmt import date_text, hhmm_text, human_day, human_range, human_time, price_breakdown, when, window_text
from app.clock import IST


def t(h, m=0, d=4):
    return datetime(2026, 11, d, h, m, tzinfo=IST)


@pytest.mark.parametrize("h,m,want", [(0, 0, "12 am"), (9, 0, "9 am"), (12, 0, "12 pm"), (14, 0, "2 pm"), (14, 30, "2:30 pm"), (23, 45, "11:45 pm")])
def test_human_time(h, m, want):
    assert human_time(t(h, m)) == want


@pytest.mark.parametrize("a,b,want", [((9,), (11,), "9–11 am"), ((11,), (13,), "11 am–1 pm"), ((14,), (16,), "2–4 pm"),
                                      ((16,), (18,), "4–6 pm"), ((10, 30), (12,), "10:30 am–12 pm")])
def test_human_range_collapses_the_meridiem_only_when_shared(a, b, want):
    assert human_range(t(*a), t(*b)) == want


def test_when_day_and_windows():
    assert when(t(14).isoformat(), t(16).isoformat()) == "Wed 4 Nov, 2–4 pm"
    assert when(t(14, 30).isoformat()) == "Wed 4 Nov, 2:30 pm"
    assert human_day(t(9, d=2)) == "Mon 2 Nov"  # no leading zero on the day
    assert hhmm_text("14:00") == "2 pm" and hhmm_text("09:30") == "9:30 am"
    assert window_text("14:00", "18:00") == "2 pm to 6 pm" and window_text("14:00", None) == "after 2 pm"
    assert window_text("18:00", "23:59") == "after 6 pm" and window_text(None, "23:59") is None  # end of day = open
    assert window_text(None, "12:00") == "before 12 pm" and window_text(None, None, True) == "any time" and window_text(None, None) is None
    assert date_text("2026-11-02", "2026-11-02") == "Mon 2 Nov" and date_text("2026-11-04", "2026-11-06") == "Wed 4 Nov to Fri 6 Nov"


@pytest.mark.parametrize("base,total", [(1100, 1350), (1100, 1100), (699, 949), (1000, 1100), (1200, 1300), (900, 2000)])
def test_price_breakdown_always_adds_up_and_starts_with_the_listed_price(base, total):
    items = price_breakdown(base, total)
    assert items[0] == {"label": "Service", "amount": base}
    assert sum(i["amount"] for i in items) == total and all(i["amount"] > 0 for i in items)
    assert (len(items) == 1) == (base == total)


def test_no_24_hour_times_reach_the_user_in_the_booking_flow():
    from tests.test_orchestrator import H, find_steps, FIRST_MSG
    h = H(forced_book_outcomes=("success",), price_drift_prob=1.0, price_drift=250)
    aid = h.to_card()
    card = h.o.card(h.sid)
    shown = [" ".join(card["lines"]), h.o.approve("user_demo", aid).reply]
    assert not any(re.search(r"\b(0\d|1[3-9]|2[0-3]):[0-5]\d\b", s) for s in shown), shown
