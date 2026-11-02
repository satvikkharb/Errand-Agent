from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.clock import Clock


class BudgetExceeded(Exception):
    """A hard limit was hit. reason is one of: limit_turns | limit_tools | timeout."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class RunBudget:
    """Hard limits for one agent run. The last few tool calls are reserved for reconciling
    an ambiguous booking, so a limit can never strand us in 'did it book or not?'."""

    clock: Clock
    max_turns: int = 8
    max_tool_calls: int = 12
    reserve_for_reconcile: int = 2
    timeout_s: int = 45
    turns: int = 0
    tool_calls: int = 0
    started: datetime = field(init=False)

    def __post_init__(self):
        self.started = self.clock.now()

    @property
    def deadline(self) -> datetime:
        return self.started + timedelta(seconds=self.timeout_s)

    def _check_time(self) -> None:
        if self.clock.now() > self.deadline:
            raise BudgetExceeded("timeout")

    def charge_turn(self) -> None:
        self._check_time()
        if self.turns >= self.max_turns:
            raise BudgetExceeded("limit_turns")
        self.turns += 1

    def charge_tool(self, reconcile: bool = False) -> None:
        self._check_time()
        limit = self.max_tool_calls if reconcile else self.max_tool_calls - self.reserve_for_reconcile
        if self.tool_calls >= limit:
            raise BudgetExceeded("limit_tools")
        self.tool_calls += 1
