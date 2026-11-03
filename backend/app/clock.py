from datetime import datetime, timedelta, timezone
from typing import Protocol

IST = timezone(timedelta(hours=5, minutes=30))


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """Injectable clock so tests and evals never depend on the real date."""

    def __init__(self, start: datetime):
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


class OffsetClock:
    """Starts at a chosen instant (the day the data was seeded for) and then runs in real time, so
    "today" stays on the seeded calendar while holds still expire after 10 real minutes."""

    def __init__(self, start: datetime, anchor: datetime | None = None, loop_hours: float | None = None, now_fn=None):
        """anchor: the real instant that counts as `start`. Without it the clock starts when THIS process starts, which is
        right locally but wrong on serverless, where every cold instance would restart "today". With a fixed anchor every
        instance computes the same time. loop_hours: wrap back to `start` after that many hours, so a deployed demo stays
        on the seeded Monday instead of drifting to Tuesday (which would change what a booking does)."""
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._start, self._t0, self._loop = start, anchor or self._now_fn(), loop_hours

    def now(self) -> datetime:
        elapsed = self._now_fn() - self._t0
        if self._loop:
            elapsed = elapsed % timedelta(hours=self._loop)
        return self._start + elapsed
