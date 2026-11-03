"""The evals must be able to FAIL: every injected bug has to be caught by at least one scenario."""
from pathlib import Path

import pytest

import evals.scenarios  # noqa: F401
from evals.harness import run_all
from evals.mutations import MUTATIONS, render, run_mutations

OUT = Path(__file__).resolve().parent.parent / "evals" / "results"


@pytest.fixture(scope="module")
def caught():
    assert all(r.passed for r in run_all().values()), "the unmutated system must pass before mutating it"
    res = run_mutations()
    (OUT).mkdir(parents=True, exist_ok=True)
    (OUT / "MUTATION_CHECK.md").write_text(render(res))
    return res


@pytest.mark.parametrize("mid", [m.id for m in MUTATIONS])
def test_injected_bug_is_caught(mid, caught):
    assert caught[mid], f"no scenario noticed the bug '{mid}': the evals do not protect that property"


def test_the_system_is_restored_after_mutating(caught):
    assert all(r.passed for r in run_all().values())
