"""The demo days: what a booking does depends on the DAY it is for. Fixed. Not a setting, not in the database."""
import time
from datetime import date, datetime

import pytest

from app.clock import IST, FixedClock, OffsetClock, SystemClock
from app.config import Settings
from app.container import make_clock
from app.tools.config import ScenarioConfig
from app.tools.day_schedule import DAYS, day_scenario
from app.tools.errors import ToolTimeout
from app.tools.provider_tools import ProviderTools
from app.tools.store import InMemoryStore
from evals.harness import Env, fill
from app.llm import say

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
D = lambda n: f"2026-11-{n:02d}"  # noqa: E731


def tools(store=None):
    store = store or InMemoryStore()
    return ProviderTools(store, FixedClock(NOW), user_id="user_demo"), store


def free_slot(store, day: str, service="plumber", area="Indiranagar"):
    """Any free, still-in-the-future slot on that day, whatever the provider."""
    for p in store.providers(service, area):
        for s in store.free_slots(p["id"], datetime.fromisoformat(f"{day}T00:00:00+05:30"), datetime.fromisoformat(f"{day}T23:59:59+05:30")):
            if s["start_at"] > NOW:
                return p, s
    raise AssertionError(f"no free slot on {day}")


# ================================================================ each day is its own situation
def test_the_calendar_is_fixed_and_every_seeded_day_has_a_distinct_purpose():
    assert [d.isoformat() for d in DAYS] == [D(n) for n in range(2, 10)]
    assert [s.key for s in DAYS.values()] == ["happy", "timeout-after-success", "slot-taken", "price-differs",
                                              "timeout-then-retry", "reschedule-conflict", "never-confirms", "happy-again"]
    assert day_scenario(date(2027, 1, 1)).key == "happy"  # outside the seeded calendar: just works


def test_today_is_the_happy_flow():
    t, store = tools()
    p, s = free_slot(store, D(2))
    h = t.hold_slot(p["id"], s["id"])
    assert h["quoted_price"] == p["base_price"]  # no surprise price
    assert t.book(h["hold_id"], "k")["status"] == "success"


def test_tomorrow_times_out_but_the_booking_really_happened():
    t, store = tools()
    p, s = free_slot(store, D(3))
    h = t.hold_slot(p["id"], s["id"])
    with pytest.raises(ToolTimeout):
        t.book(h["hold_id"], "k")
    st = t.get_booking_status(idempotency_key="k")
    assert st["status"] == "booked"
    assert t.book(h["hold_id"], "k")["booking_id"] == st["booking_id"]  # the retry replays the same booking


def test_the_day_after_the_slot_is_taken():
    t, store = tools()
    for service in ("plumber", "electrician", "ac_servicing"):  # the service makes no difference: only the day does
        p, s = free_slot(store, D(4), service)
        h = t.hold_slot(p["id"], s["id"])
        assert t.book(h["hold_id"], f"k-{service}")["status"] == "slot_taken"


def test_thursday_quotes_a_higher_price_at_hold_time_then_books():
    t, store = tools()
    p, s = free_slot(store, D(5))
    h = t.hold_slot(p["id"], s["id"])
    assert h["quoted_price"] == p["base_price"] + 250
    assert t.book(h["hold_id"], "k")["status"] == "success"


def test_friday_times_out_without_booking_then_the_retry_works():
    t, store = tools()
    p, s = free_slot(store, D(6))
    h = t.hold_slot(p["id"], s["id"])
    with pytest.raises(ToolTimeout):
        t.book(h["hold_id"], "k")
    assert t.get_booking_status(idempotency_key="k")["status"] == "not_found"  # nothing was booked
    assert t.book(h["hold_id"], "k")["status"] == "success"  # same key, second attempt


def test_saturday_books_fine_but_moving_a_booking_into_it_always_conflicts():
    t, store = tools()
    p, s = free_slot(store, D(7))
    assert t.book(t.hold_slot(p["id"], s["id"])["hold_id"], "k")["status"] == "success"
    sat = store.free_slots("ac_01", datetime(2026, 11, 7, tzinfo=IST), datetime(2026, 11, 8, tzinfo=IST))[0]
    assert t.reschedule("bk_004", sat["id"], "r")["status"] == "slot_conflict"  # even though the slot is free
    assert store.booking("bk_004")["slot_id"] == "ac_01_1105_14"  # the original is untouched
    fri = store.free_slots("ac_01", datetime(2026, 11, 6, tzinfo=IST), datetime(2026, 11, 7, tzinfo=IST))[0]
    assert t.reschedule("bk_004", fri["id"], "r2")["status"] == "rescheduled"  # other days reschedule fine


def test_sunday_never_confirms_every_attempt_times_out():
    t, store = tools()
    p, s = free_slot(store, D(8))
    h = t.hold_slot(p["id"], s["id"])
    for _ in range(3):
        with pytest.raises(ToolTimeout):
            t.book(h["hold_id"], "k")
    assert t.get_booking_status(hold_id=h["hold_id"], idempotency_key="k")["status"] == "not_booked"


# ================================================================ nothing can switch it
def test_there_is_no_setting_and_no_database_value_that_changes_the_flow():
    assert not hasattr(Settings(), "scenario_mode")
    import app.container as container
    assert not hasattr(container, "scenario_config")
    assert ScenarioConfig.from_row({"forced_book_outcome": "success", "price_drift_prob": 1.0}) == ScenarioConfig()
    store = InMemoryStore()
    store._config.update(forced_book_outcome="success", price_drift_prob=0.0)  # someone edits the table
    t = ProviderTools(store, FixedClock(NOW), user_id="user_demo")
    p, s = free_slot(store, D(4))
    assert t.book(t.hold_slot(p["id"], s["id"])["hold_id"], "k")["status"] == "slot_taken"  # still the day's outcome


# ================================================================ end to end, through the real orchestrator
def day_flow(day: int, window=("09:00", "18:00")):
    env = Env(forced=(), drift=None)  # no hooks: the day schedule is all there is
    u = env.user()
    u.says("A plumber please", fill(date_start=D(day), window_start=window[0], window_end=window[1]), say("Here are your options."))
    u.says("Book the top one", env.propose())
    return env, u, u.taps_approve()


def test_booking_today_just_works():
    env, u, out = day_flow(2, window=("11:00", "18:00"))
    assert out.reply.startswith("Booked and verified") and len(env.new_bookings()) == 1
    assert not any("taxes and fees" in out.reply for _ in [0]) and out.state == "DONE"


def test_booking_tomorrow_is_the_timeout_that_actually_booked():
    env, u, out = day_flow(3)
    assert out.reply.startswith("Booked and verified") and len(env.new_bookings()) == 1
    assert env.spans("tool_call", "exec.book")[0]["error"] == "ToolTimeout"


def test_booking_the_day_after_hits_a_taken_slot():
    env, u, out = day_flow(4)
    assert "just taken" in out.reply and env.new_bookings() == [] and out.state == "SEARCH"


def test_booking_thursday_shows_taxes_and_fees_on_the_card():
    env, u, out = day_flow(5)
    card = env.app.approval(env.attempts()[0]["approval_id"])["payload"]
    assert card["price"] == card["search_price"] + 250 and "taxes and fees" in out.reply


def test_booking_friday_retries_with_the_same_key():
    env, u, out = day_flow(6)
    calls = env.spans("tool_call", "exec.book")
    assert out.reply.startswith("Booked and verified") and len(calls) == 2 and len({c["input"]["args"][1] for c in calls}) == 1
    assert len(env.new_bookings()) == 1


def test_booking_sunday_never_confirms_and_the_agent_never_claims_it_did():
    env, u, out = day_flow(8)
    assert "Booked" not in out.reply and out.state == "RECONCILE" and env.new_bookings() == []
    assert env.attempts()[0]["status"] == "unknown"


# ================================================================ the clock: "today" is the day the data was seeded for
def test_default_clock_is_the_seeded_day_and_keeps_running():
    for raw in ("", "   ", "2026-11-02T10:00:00+05:30"):  # an empty SIM_NOW means the default, not real time
        c = make_clock(Settings(sim_now=raw))
        assert isinstance(c, OffsetClock) and c.now().date().isoformat() == "2026-11-02" and c.now().hour == 10
    c = make_clock(Settings())
    first = c.now()
    time.sleep(0.02)
    assert c.now() > first  # runs in real time, so a 10-minute hold still expires
    assert isinstance(make_clock(Settings(sim_freeze=True)), FixedClock)
    assert isinstance(make_clock(Settings(sim_now="system")), SystemClock)


def test_today_has_slots_so_anytime_after_2pm_today_finds_something():
    store = InMemoryStore()
    res = ProviderTools(store, FixedClock(NOW)).search_providers("plumber", "Koramangala", {"start": D(2), "end": D(2)})["providers"]
    starts = {s["start"][11:16] for p in res for s in p["slots"]}
    assert starts and starts <= {"11:00", "14:00", "16:00"}  # nothing already in the past (the 09:00 slots)
