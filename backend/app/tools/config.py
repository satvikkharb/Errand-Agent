from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioConfig:
    """How the simulated world behaves. In the running app this is ALWAYS the defaults below: what a booking
    does is decided by the day it is for (see day_schedule.py) and nothing in the environment or database
    changes it. The non-default fields are test hooks, only ever set in code by tests and evals."""

    seed: int = 42
    cancel_first_503: bool = True  # the assignment: the first cancel call fails with a transient 503
    hold_minutes: int = 10
    # ---- test hooks (never set by the app) ----
    forced_book_outcomes: tuple = ()  # overrides the day schedule: success | slot_taken | timeout_success | timeout_fail
    price_drift_prob: float | None = None  # None = the day schedule decides; a number forces random-per-slot drift
    price_drift: int = 250
    reschedule_conflict_slots: frozenset = frozenset()  # extra always-conflicting slots (the Saturday rule is built in)

    @classmethod
    def from_row(cls, row: dict | None = None) -> "ScenarioConfig":
        """Kept so callers need no change. The scenario_config table is no longer consulted: it is not a knob."""
        return cls()
