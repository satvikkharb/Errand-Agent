"""SCENARIO_MODE: a deterministic happy path for the first demo, without touching the assignment's random mode."""
from datetime import datetime

import pytest

from app.agent.app_store import InMemoryAppStore
from app.agent.orchestrator import Orchestrator
from app.clock import IST, FixedClock
from app.config import Settings
from app.container import scenario_config
from app.llm import ScriptedLLM, call, say
from app.tools.store import InMemoryStore
from evals.harness import fill

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)


def book_quickfix_nine_am(mode):
    """The slot that always fails in random mode: QuickFix Plumbing, tomorrow 09:00."""
    store, app = InMemoryStore(), InMemoryAppStore()
    llm = ScriptedLLM([fill(), say("Options above."), call("propose_book", provider_id="pl_01", slot_id="pl_01_1103_09")])
    o = Orchestrator(app, store, FixedClock(NOW), llm, tools_config=scenario_config(Settings(scenario_mode=mode), store),
                     sleep=lambda s: None)
    r = o.handle_message("user_demo", "A plumber tomorrow morning")
    r = o.handle_message("user_demo", "Book QuickFix at 9", r.session_id)
    assert r.approval_card, r.reply
    return o.approve("user_demo", r.approval_card["approval_id"]), r.approval_card, store


def test_random_mode_keeps_the_assignments_lottery():
    assert scenario_config(Settings(scenario_mode="random"), InMemoryStore()) is None
    out, _, store = book_quickfix_nine_am("random")
    assert "just taken" in out.reply  # this exact slot is a deterministic slot_taken in the seeded world
    assert not [b for b in store._bookings.values() if b["provider_id"] == "pl_01" and b["status"] == "confirmed"]


def test_happy_mode_books_that_same_slot_cleanly_at_the_price_shown():
    cfg = scenario_config(Settings(scenario_mode="happy"), InMemoryStore())
    assert cfg.forced_book_outcomes == ("success",) and cfg.price_drift_prob == 0.0
    out, card, store = book_quickfix_nine_am("happy")
    assert out.reply.startswith("Booked and verified") and "Price changed" not in " ".join(card["lines"])
    assert [b["price"] for b in store._bookings.values() if b["provider_id"] == "pl_01" and b["status"] == "confirmed"] == [1200]


def test_happy_mode_keeps_the_other_simulated_behaviours():
    cfg = scenario_config(Settings(scenario_mode="happy"), InMemoryStore())
    assert cfg.cancel_first_503 is True and cfg.hold_minutes == 10  # only booking outcomes and price drift are forced


@pytest.mark.parametrize("mode", ["random", "", "anything-else"])
def test_unknown_modes_fall_back_to_the_assignments_behaviour(mode):
    assert scenario_config(Settings(scenario_mode=mode), InMemoryStore()) is None


# ---------------------------------------------------------------- the clock: "today" is the day the data was seeded for
def test_default_clock_is_the_seeded_day_and_keeps_running():
    from app.clock import FixedClock, OffsetClock, SystemClock
    from app.container import make_clock
    for raw in ("", "   ", "2026-11-02T10:00:00+05:30"):  # an empty SIM_NOW means the default, not real time
        c = make_clock(Settings(sim_now=raw))
        assert isinstance(c, OffsetClock) and c.now().date().isoformat() == "2026-11-02" and c.now().hour == 10
    c = make_clock(Settings())
    first = c.now()
    import time
    time.sleep(0.02)
    assert c.now() > first  # runs in real time, so a 10-minute hold still expires
    assert isinstance(make_clock(Settings(sim_freeze=True)), FixedClock)
    assert isinstance(make_clock(Settings(sim_now="system")), SystemClock)


def test_today_has_slots_so_anytime_after_2pm_today_finds_something():
    from app.tools.provider_tools import ProviderTools
    store = InMemoryStore()
    t = ProviderTools(store, FixedClock(NOW))  # 2 Nov 10:00 IST
    res = t.search_providers("plumber", "Koramangala", {"start": "2026-11-02", "end": "2026-11-02"})["providers"]
    starts = {s["start"][11:16] for p in res for s in p["slots"]}
    assert starts and starts <= {"11:00", "14:00", "16:00"}  # nothing already in the past (the 09:00 slots)
    assert all(s["id"].split("_")[2] == "1102" for p in res for s in p["slots"])
