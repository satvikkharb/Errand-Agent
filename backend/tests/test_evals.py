"""Runs every eval scenario offline and GENERATES the eval table (evals/results/EVAL_TABLE.md)."""
from pathlib import Path

import pytest

import evals.scenarios  # noqa: F401  (registers the scenarios)
from evals.harness import REQUIRED_COVERAGE, SCENARIOS, run_all, write_report

OUT = Path(__file__).resolve().parent.parent / "evals" / "results"


@pytest.fixture(scope="session")
def results():
    res = run_all()
    write_report(res, OUT)  # the table is produced by the run itself, never edited by hand
    return res


@pytest.mark.parametrize("sid", [s.id for s in SCENARIOS])
def test_scenario(sid, results):
    r = results[sid]
    assert r.passed, f"\n{r.failure_text()}"


def test_at_least_twenty_scenarios_with_unique_ids():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) >= 20 and len(ids) == len(set(ids))


def test_every_scenario_the_assignment_names_is_covered_and_passing(results):
    for tag, label in REQUIRED_COVERAGE.items():
        ids = [r.id for r in results.values() if tag in r.covers]
        assert ids, f"no scenario covers: {label}"
        assert all(results[i].passed for i in ids), f"{label} has a failing scenario"


def test_the_table_was_generated_from_this_run(results):
    table = (OUT / "EVAL_TABLE.md").read_text()
    assert all(f"`{sid}`" in table for sid in results)
    assert "Invariants audited on every scenario" in table and "Coverage of the scenarios the assignment names" in table
