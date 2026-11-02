from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.clock import IST, FixedClock
from app.tools.config import ScenarioConfig
from app.tools.errors import ToolTimeout, ToolUnavailable
from app.tools.provider_tools import ProviderTools
from app.tools.sim import provider_cancels
from app.tools.store import InMemoryStore

NOW = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
DAY = {"start": "2026-11-03", "end": "2026-11-03"}  # date-only end is inclusive


def make(user_id="user_demo", **cfg):
    store, clock = InMemoryStore(), FixedClock(NOW)
    base = ScenarioConfig.from_row(store.config())
    return ProviderTools(store, clock, replace(base, **cfg), user_id=user_id), store, clock


def first_slot(tools, service="plumber", area="Indiranagar"):
    p = tools.search_providers(service, area, DAY)["providers"][0]
    return p, p["slots"][0]["id"]


def hold(tools, **kw):
    p, sid = first_slot(tools, **kw)
    return tools.hold_slot(p["id"], sid)


# ---- search / get_provider ----
def test_search_returns_3_to_8_in_range_and_free_only():
    t, store, _ = make()
    ps = t.search_providers("Plumber", "indiranagar", DAY)["providers"]
    assert 3 <= len(ps) <= 8
    for p in ps:
        for s in p["slots"]:
            assert s["start"].startswith("2026-11-03")
            assert store.slot(s["id"])["status"] == "free"


def test_search_unknown_area_is_empty_not_error():
    t, *_ = make()
    assert t.search_providers("plumber", "Mars", DAY) == {"providers": []}


def test_get_provider_has_prompt_injection_review():
    t, *_ = make()
    texts = " ".join(r["text"] for r in t.get_provider("pl_03")["reviews"]).lower()
    assert "ignore all previous instructions" in texts
    assert t.get_provider("nope") == {"status": "not_found"}


# ---- hold ----
def test_hold_expires_in_10_minutes_and_blocks_second_hold():
    t, *_ = make()
    p, sid = first_slot(t)
    h = t.hold_slot(p["id"], sid)
    assert h["status"] == "held"
    assert datetime.fromisoformat(h["expires_at"]) == NOW + timedelta(minutes=10)
    assert t.hold_slot(p["id"], sid)["status"] == "slot_unavailable"


def test_quoted_price_drift_is_configurable():
    t, *_ = make(price_drift_prob=1.0, price_drift=250)
    p, sid = first_slot(t)
    assert t.hold_slot(p["id"], sid)["quoted_price"] == p["price"] + 250
    t2, *_ = make(price_drift_prob=0.0)
    p, sid = first_slot(t2)
    assert t2.hold_slot(p["id"], sid)["quoted_price"] == p["price"]


def test_expired_hold_frees_slot_and_book_says_hold_expired():
    t, _, clock = make(forced_book_outcomes=("success",))
    h = hold(t)
    clock.advance(minutes=11)
    assert t.book(h["hold_id"], "k1")["status"] == "hold_expired"
    assert any(s["id"] == h["slot_id"] for p in t.search_providers("plumber", "Indiranagar", DAY)["providers"]
               for s in p["slots"])


# ---- book ----
def test_book_success_and_replay_does_not_duplicate():
    t, *_ = make(forced_book_outcomes=("success",))
    h = hold(t)
    r1 = t.book(h["hold_id"], "k1")
    assert r1["status"] == "success"
    r2 = t.book(h["hold_id"], "k1")
    assert r2["replayed"] and r2["booking_id"] == r1["booking_id"]
    mine = [b for b in t.list_bookings()["bookings"] if b["id"] == r1["booking_id"]]
    assert len(mine) == 1
    assert t.book(h["hold_id"], "other-key") == {"status": "error", "code": "hold_already_used"}


def test_book_slot_taken():
    t, *_ = make(forced_book_outcomes=("slot_taken",))
    h = hold(t)
    assert t.book(h["hold_id"], "k1")["status"] == "slot_taken"
    assert t.get_booking_status(idempotency_key="k1") == {"status": "failed", "reason": "slot_taken"}


def test_timeout_after_success_is_found_via_status_and_retry_does_not_duplicate():
    t, *_ = make(forced_book_outcomes=("timeout_success",))
    h = hold(t)
    with pytest.raises(ToolTimeout):
        t.book(h["hold_id"], "k1")
    st = t.get_booking_status(idempotency_key="k1")
    assert st["status"] == "booked"
    retry = t.book(h["hold_id"], "k1")
    assert retry["status"] == "success" and retry["booking_id"] == st["booking_id"]
    assert sum(b["id"] == st["booking_id"] for b in t.list_bookings()["bookings"]) == 1


def test_timeout_without_booking_then_retry_succeeds():
    t, *_ = make(forced_book_outcomes=("timeout_fail", "success"))
    h = hold(t)
    with pytest.raises(ToolTimeout):
        t.book(h["hold_id"], "k1")
    assert t.get_booking_status(idempotency_key="k1")["status"] == "not_found"
    st = t.get_booking_status(hold_id=h["hold_id"])
    assert st["status"] == "not_booked" and st["hold"] == "active"
    assert t.book(h["hold_id"], "k1")["status"] == "success"


def test_book_outcome_mix_is_about_70_15_15_and_half_of_timeouts_booked():
    t, store, _ = make()
    slots = [s for s in store._slots.values() if s["status"] == "free"]
    counts = {"success": 0, "slot_taken": 0, "timeout": 0}
    timeout_booked = 0
    for i, s in enumerate(slots):
        h = t.hold_slot(s["provider_id"], s["id"])
        try:
            counts[t.book(h["hold_id"], f"k{i}")["status"]] += 1
        except ToolTimeout:
            counts["timeout"] += 1
            timeout_booked += t.get_booking_status(idempotency_key=f"k{i}")["status"] == "booked"
    n = len(slots)
    assert abs(counts["success"] / n - 0.70) < 0.07
    assert abs(counts["slot_taken"] / n - 0.15) < 0.07
    assert abs(counts["timeout"] / n - 0.15) < 0.07
    assert abs(timeout_booked / counts["timeout"] - 0.5) < 0.2


def test_same_seed_gives_same_outcomes():
    def run():
        t, store, _ = make()
        out = []
        for s in [s for s in store._slots.values() if s["status"] == "free"][:40]:
            h = t.hold_slot(s["provider_id"], s["id"])
            try:
                out.append(t.book(h["hold_id"], "k")["status"])
            except ToolTimeout:
                out.append("timeout")
        return out
    assert run() == run()


# ---- cancel ----
def test_cancel_first_call_503_then_retry_succeeds_and_frees_slot():
    t, store, _ = make()
    with pytest.raises(ToolUnavailable):
        t.cancel("bk_004", "c1")
    assert t.cancel("bk_004", "c1")["status"] == "cancelled"
    assert t.cancel("bk_004", "c1")["replayed"] is True
    assert store.slot("ac_01_1105_14")["status"] == "free"


def test_cancel_edge_cases():
    t, *_ = make(cancel_first_503=False)
    assert t.cancel("bk_001", "c1")["status"] == "not_cancellable"  # completed
    assert t.cancel("bk_004", "c2")["status"] == "cancelled"
    assert t.cancel("bk_004", "c3")["status"] == "already_cancelled"
    assert t.cancel("nope", "c4")["status"] == "not_found"
    other, *_ = make(user_id="user_other", cancel_first_503=False)
    assert other.cancel("bk_005", "c5")["status"] == "not_found"  # not their booking


# ---- reschedule ----
def test_reschedule_conflict_keeps_old_booking():
    t, store, _ = make()
    r = t.reschedule("bk_004", "ac_01_1107_09", "r1")
    assert r["status"] == "slot_conflict"
    b = store.booking("bk_004")
    assert b["slot_id"] == "ac_01_1105_14" and b["status"] == "confirmed"


def test_reschedule_success_moves_slot():
    t, store, _ = make()
    new = store.free_slots("ac_01", datetime(2026, 11, 6, tzinfo=IST), datetime(2026, 11, 7, tzinfo=IST))[0]
    r = t.reschedule("bk_004", new["id"], "r1")
    assert r["status"] == "rescheduled"
    assert store.slot("ac_01_1105_14")["status"] == "free"
    assert store.slot(new["id"])["status"] == "booked"
    assert store.booking("bk_004")["slot_id"] == new["id"]
    assert t.reschedule("bk_004", new["id"], "r1")["replayed"] is True


# ---- list / sim ----
def test_list_bookings_and_other_user_sees_nothing():
    t, *_ = make()
    bs = t.list_bookings("user_demo")["bookings"]
    assert len(bs) == 5 and {"id", "provider_name", "service", "slot", "status"} <= bs[0].keys()
    assert t.list_bookings("user_other") == {"bookings": []}


def test_provider_cancellation_event_is_deduped():
    _, store, _ = make()
    assert provider_cancels(store, "bk_004") == {"event_inserted": True, "booking_id": "bk_004"}
    assert store.booking("bk_004")["status"] == "cancelled_by_provider"
    assert provider_cancels(store, "bk_004") is None
    assert len(store.events()) == 1
