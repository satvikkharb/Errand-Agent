"""Things that only matter once the backend runs on serverless: a shared clock, small pools, traces that get sent."""
from datetime import datetime, timedelta, timezone

from langsmith import Client

from app.agent.telemetry import Telemetry
from app.clock import IST, OffsetClock
from app.config import Settings
from app.container import make_clock
from tests.support.fake_langsmith import FakeLangSmith
from tests.test_orchestrator import H

START = datetime(2026, 11, 2, 10, 0, tzinfo=IST)
REAL = datetime(2026, 11, 20, 8, 0, tzinfo=timezone.utc)


class Now:  # a controllable real clock
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def test_without_an_anchor_each_process_starts_its_own_clock_the_local_behaviour():
    now = Now(REAL)
    a = OffsetClock(START, now_fn=now)
    now.t += timedelta(hours=3)
    b = OffsetClock(START, now_fn=now)  # a "second instance" started three hours later
    assert a.now() == START + timedelta(hours=3) and b.now() == START


def test_with_an_anchor_every_instance_agrees_whenever_it_started():
    now = Now(REAL)
    anchor = REAL - timedelta(minutes=30)  # the moment the deployment counted as 2 Nov 10:00
    a = OffsetClock(START, anchor=anchor, now_fn=now)
    now.t += timedelta(hours=3)
    b = OffsetClock(START, anchor=anchor, now_fn=now)  # cold start much later
    assert a.now() == b.now() == START + timedelta(hours=3, minutes=30)


def test_the_looping_clock_keeps_today_on_the_seeded_monday():
    now = Now(REAL)
    c = OffsetClock(START, loop_hours=10, now_fn=now)
    seen = set()
    for _ in range(60):  # three real days, sampled every hour or so
        now.t += timedelta(hours=1, minutes=13)
        seen.add(c.now().date())
        assert START <= c.now() < START + timedelta(hours=10)
    assert seen == {START.date()}  # never drifts to Tuesday, so the day-based booking flows keep working


def test_the_clock_is_built_from_settings():
    plain = make_clock(Settings(_env_file=None))
    assert plain.now() >= START and plain.now() < START + timedelta(minutes=1)
    anchored = make_clock(Settings(_env_file=None, sim_anchor="2020-01-01T00:00:00+00:00", sim_loop_hours=10))
    assert START <= anchored.now() < START + timedelta(hours=10)  # long ago anchor + loop stays in the window


def test_pool_sizes_are_configurable_for_serverless(monkeypatch):
    monkeypatch.setenv("DB_POOL_MAX", "3")
    monkeypatch.setenv("DB_POOL_MIN", "1")
    s = Settings(_env_file=None)
    assert (s.db_pool_min, s.db_pool_max) == (1, 3)


def test_on_serverless_the_trace_is_sent_before_the_reply_returns():
    fs = FakeLangSmith()
    try:
        tel = Telemetry(Client(api_url=fs.url, api_key="k"), "errand-test", flush_wait_s=8)
        h = H(forced_book_outcomes=("success",))
        h.o.telemetry = h.o.proactive.telemetry = tel
        h.o.approve("user_demo", h.to_card())
        assert any(f["key"] == "booking_verified" for f in fs.feedback)  # no drain(): already delivered
    finally:
        fs.close()
