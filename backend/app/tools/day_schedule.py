"""What happens when you book, decided by WHICH DAY you book. Fixed, not configurable.

Each weekday is a different situation, so a demo is just a choice of day. The cycle repeats every week
(the simulated "today" is Mon 2 Nov 2026, so next Tuesday behaves like this Tuesday):

  Monday     happy flow            the booking just works at the listed price
  Tuesday    timeout, but it DID book   the call times out; the status check finds the booking
  Wednesday  slot taken            someone else grabs the slot before you confirm
  Thursday   price differs         the quote at hold time is higher (taxes and fees at checkout), then it books
  Friday     timeout, then retry   the first call times out and booked nothing; the retry works
  Saturday   reschedule conflict   booking works, but moving a booking INTO a Saturday slot always conflicts
  Sunday     never confirms        every attempt times out; the outcome stays unknown until the hold expires

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


WEEK: tuple[DayScenario, ...] = (  # index = weekday(): Monday is 0
    DayScenario("happy", "Happy flow", ("success",)),
    DayScenario("timeout-after-success", "Timeout, but it booked", ("timeout_success",)),
    DayScenario("slot-taken", "Slot taken", ("slot_taken",)),
    DayScenario("price-differs", "Price differs at checkout", ("success",), quote_extra=250),
    DayScenario("timeout-then-retry", "Timeout, then retry", ("timeout_fail", "success")),
    DayScenario("reschedule-conflict", "Reschedule conflict", ("success",), reschedule_conflict=True),
    DayScenario("never-confirms", "Never confirms", ("timeout_fail",)),
)


def day_scenario(d: date) -> DayScenario:
    return WEEK[d.weekday()]
