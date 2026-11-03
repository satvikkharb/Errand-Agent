"""Do the evals have teeth? Break the system on purpose and require the suite to notice.

Each mutation re-introduces a realistic bug (the kind this system exists to prevent) for the duration of
one full eval run. A mutation that no scenario catches would mean the evals prove nothing about that
property, so the test fails.
"""
import uuid
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Callable
from unittest.mock import patch

from pydantic import BaseModel, ConfigDict

from app.agent import approvals as approvals_mod
from app.agent import gateway as gateway_mod
from app.agent import orchestrator as orch_mod
from app.agent.app_store import InMemoryAppStore
from app.agent.context import ContextBuilder
from app.agent.executor import AMBIGUOUS, ExecResult, Executor
from evals.harness import run_all


@dataclass
class Mutation:
    id: str
    bug: str
    apply: Callable[[ExitStack], None]


def _begin_attempt_without_checks(self, approval_id, payload_hash, hold_id, now):
    a = self._approvals[approval_id]
    a.update(status="consumed", consumed_at=now)
    row = {"id": str(uuid.uuid4()), "approval_id": approval_id, "action": a["action"],
           "idempotency_key": f"idem_{approval_id}", "status": "started", "hold_id": hold_id, "booking_id": None,
           "tool_response": None, "retry_count": 0, "started_at": now, "resolved_at": None}
    self._attempts[row["id"]] = row
    return dict(row)


def _approve_without_checks(deps, approval_id, user_id):
    a = deps.app.approval(approval_id)
    if not a:
        return {"ok": False, "error": "not_found"}
    deps.app.update_approval(approval_id, status="approved", decided_at=deps.clock.now())
    return {"ok": True, "approval": deps.app.approval(approval_id)}


class _BookArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hold_id: str
    idempotency_key: str


def _mut_single_use(stack):
    stack.enter_context(patch.object(approvals_mod, "approve", _approve_without_checks))
    stack.enter_context(patch.object(InMemoryAppStore, "begin_attempt", _begin_attempt_without_checks))


def _mut_random_key(stack):
    orig = InMemoryAppStore.begin_attempt

    def begin(self, *a, **kw):
        row = orig(self, *a, **kw)
        self._attempts[row["id"]]["idempotency_key"] = f"idem_{uuid.uuid4()}"  # a fresh key per attempt
        return dict(self._attempts[row["id"]])
    stack.enter_context(patch.object(InMemoryAppStore, "begin_attempt", begin))


def _mut_no_sanitizer(stack):
    stack.enter_context(patch.object(gateway_mod, "sanitize", lambda obj, *a, **k: obj))
    stack.enter_context(patch.object(gateway_mod, "clean_text", lambda text, n=300: (text, False)))


def _mut_no_redaction(stack):
    stack.enter_context(patch.object(InMemoryAppStore, "redact_messages", lambda self, user_id, needle: 0))


def _mut_guesses_leak(stack):
    def active(self, user_id):  # serve everything that has a value, confirmed or not
        return [m for m in self.memories(user_id) if m["value"] is not None and m["superseded_by"] is None]
    stack.enter_context(patch.object(InMemoryAppStore, "active_memories", active))


def _mut_no_reply_guard(stack):
    stack.enter_context(patch.object(orch_mod, "guard_reply", lambda text, has_pending: text))


def _mut_claims_success(stack):
    def fake_try(self, name, fn, *a, **kw):
        try:
            return self._call(name, fn, *a, **kw), False
        except AMBIGUOUS:
            return {"status": "success"}, False  # a timeout treated as success
    stack.enter_context(patch.object(Executor, "_try", fake_try))
    stack.enter_context(patch.object(Executor, "_verify_booked",
                                     lambda self, a, key: ExecResult("booked", "Booked!", "bk_fake")))


def _mut_budget_ignored(stack):
    orig = ContextBuilder.build

    def build(self, **kw):
        self.budget = 10 ** 9
        return orig(self, **kw)
    stack.enter_context(patch.object(ContextBuilder, "build", build))


def _mut_holds_never_released(stack):
    stack.enter_context(patch.object(approvals_mod, "release_hold", lambda deps, hold_id: None))


def _mut_eager_yes(stack):
    orig = orch_mod._norm
    stack.enter_context(patch.object(orch_mod, "_norm", lambda s: "yes" if s.strip().lower().startswith("yes") else orig(s)))


def _mut_book_tool_exposed(stack):
    def _t_book(self, a):
        res = self.d.tools.book(a.hold_id, a.idempotency_key)
        return gateway_mod.ToolOutcome("book", res)
    stack.enter_context(patch.dict(gateway_mod.REGISTRY, {"book": (_BookArgs, "Book a held slot.")}))
    stack.enter_context(patch.object(gateway_mod.ToolGateway, "_t_book", _t_book, create=True))


MUTATIONS = [
    Mutation("approval-not-single-use", "approvals can be reused and attempts duplicated", _mut_single_use),
    Mutation("idempotency-key-random", "a fresh idempotency key per attempt instead of one derived from the approval", _mut_random_key),
    Mutation("sanitizer-off", "provider text reaches the model unsanitized", _mut_no_sanitizer),
    Mutation("forget-skips-chat-history", "forgetting leaves the fact in the chat history", _mut_no_redaction),
    Mutation("unconfirmed-guesses-leak", "unconfirmed inferred memories are served to the model", _mut_guesses_leak),
    Mutation("reply-guard-off", "the model's false 'I booked it' is shown to the user", _mut_no_reply_guard),
    Mutation("claims-success-unverified", "a timeout is treated as success without checking the source of truth", _mut_claims_success),
    Mutation("context-budget-ignored", "the token budget is not enforced", _mut_budget_ignored),
    Mutation("holds-never-released", "slots stay held after a card is withdrawn or expires", _mut_holds_never_released),
    Mutation("typed-yes-too-eager", "'yes, but make it free' is accepted as approval", _mut_eager_yes),
    Mutation("book-tool-exposed-to-model", "the model is given the real book tool", _mut_book_tool_exposed),
]


def run_mutations() -> dict[str, list[str]]:
    """{mutation id: [scenario ids that failed while it was active]}"""
    out = {}
    for m in MUTATIONS:
        with ExitStack() as stack:
            m.apply(stack)
            out[m.id] = [sid for sid, r in run_all().items() if not r.passed]
    return out


def render(results: dict[str, list[str]]) -> str:
    lines = ["# Do the evals have teeth?", "",
             "Each row re-introduces one realistic bug for a full eval run. **Caught** means at least one scenario failed.",
             "Generated by `pytest tests/test_eval_mutations.py`.", "",
             "| Bug introduced | What it breaks | Caught | Scenarios that failed |", "|---|---|---|---|"]
    for m in MUTATIONS:
        failed = results[m.id]
        lines.append(f"| `{m.id}` | {m.bug} | {'yes' if failed else '**NO**'} | "
                     f"{', '.join(f'`{s}`' for s in failed[:6])}{' …' if len(failed) > 6 else ''} ({len(failed)}) |")
    caught = sum(1 for m in MUTATIONS if results[m.id])
    lines += ["", f"**{caught}/{len(MUTATIONS)} injected bugs were caught.**"]
    return "\n".join(lines) + "\n"
