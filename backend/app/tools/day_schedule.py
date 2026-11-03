"""What happens when you book, decided by WHICH DAY you book. Fixed, not configurable.

Each seeded day is a different situation, so a demo is just a choice of day (the simulated "today" is Mon 2 Nov):

  Mon 2 Nov  (today)     happy flow        the booking just works at the listed price
  Tue 3 Nov  (tomorrow)  timeout, but it DID book   the call times out; the status check finds the booking
  Wed 4 Nov              slot taken        someone else grabs the slot before you confirm
  Thu 5 Nov              price differs     the quote at hold time is higher (taxes and fees at checkout), then it books
  Fri 6 Nov              timeout, then retry   the first call times out and booked nothing; the retry works
  Sat 7 Nov              reschedule conflict   booking works, but moving a booking to a Saturday slot always conflicts
  Sun 8 Nov              never confirms    every attempt times out; the outcome stays unknown until the hold expires
  Mon 9 Nov              happy flow again

The booking-time outcome is a list read by attempt number (the last entry repeats).
"""
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class DayScenario:
    key: str
    title: str
    book: tuple[str, ...]  # book() outcome per attempt: success | slot_taken | timeout_success | timeout_fail
    quote_extra: int = 0  # Rs added to the quote at hold time (taxes and fees at checkout)
    reschedule_conflict: bool = False  # rescheduling INTO a slot on this day always conflicts


DAYS: dict[date, DayScenario] = {
    date(2026, 11, 2): DayScenario("happy", "Happy flow", ("success",)),
    date(2026, 11, 3): DayScenario("timeout-after-success", "Timeout, but it booked", ("timeout_success",)),
    date(2026, 11, 4): DayScenario("slot-taken", "Slot taken", ("slot_taken",)),
    date(2026, 11, 5): DayScenario("price-differs", "Price differs at checkout", ("success",), quote_extra=250),
    date(2026, 11, 6): DayScenario("timeout-then-retry", "Timeout, then retry", ("timeout_fail", "success")),
    date(2026, 11, 7): DayScenario("reschedule-conflict", "Reschedule conflict", ("success",), reschedule_conflict=True),
    date(2026, 11, 8): DayScenario("never-confirms", "Never confirms", ("timeout_fail",)),
    date(2026, 11, 9): DayScenario("happy-again", "Happy flow", ("success",)),
}
DEFAULT = DAYS[date(2026, 11, 2)]  # a day outside the seeded calendar behaves like the happy flow


def day_scenario(d: date) -> DayScenario:
    return DAYS.get(d, DEFAULT)
