import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioConfig:
    """Knobs for the simulated provider world. Same seed => same outcomes."""

    seed: int = 42
    p_success: float = 0.70
    p_slot_taken: float = 0.15  # the remaining 15% is timeout
    timeout_success_share: float = 0.5  # of timeouts, this share actually booked
    price_drift: int = 250  # Rs added to the quote when drift happens
    price_drift_prob: float = 0.3
    cancel_first_503: bool = True
    hold_minutes: int = 10
    reschedule_conflict_slots: frozenset = frozenset()
    # Tests: force book() outcomes by attempt index (last one repeats).
    # Values: success | slot_taken | timeout_success | timeout_fail
    forced_book_outcomes: tuple = ()

    @classmethod
    def from_row(cls, row: dict | None) -> "ScenarioConfig":
        if not row:
            return cls()
        bo = row.get("book_outcomes") or {}
        if isinstance(bo, str):
            bo = json.loads(bo)
        forced = row.get("forced_book_outcome")
        return cls(
            seed=row["seed"],
            p_success=bo.get("success", 0.70),
            p_slot_taken=bo.get("slot_taken", 0.15),
            timeout_success_share=bo.get("timeout_success_share", 0.5),
            price_drift=row.get("price_drift", 250),
            price_drift_prob=row.get("price_drift_prob", 0.3),
            cancel_first_503=row.get("cancel_first_503", True),
            hold_minutes=row.get("hold_minutes", 10),
            reschedule_conflict_slots=frozenset(row.get("reschedule_conflict_slots") or []),
            forced_book_outcomes=tuple(x.strip() for x in forced.split(",")) if forced else (),
        )
