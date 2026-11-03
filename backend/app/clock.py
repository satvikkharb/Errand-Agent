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

    def __init__(self, start: datetime):
        self._start, self._t0 = start, datetime.now(timezone.utc)

    def now(self) -> datetime:
        return self._start + (datetime.now(timezone.utc) - self._t0)
