"""Runs every eval scenario offline and GENERATES the eval table (evals/results/EVAL_TABLE.md)."""
from pathlib import Path

import pytest

import evals.scenarios  # noqa: F401  (registers the scenarios)
import socket

from evals.harness import REQUIRED_COVERAGE, SCENARIOS, OfflineViolation, Scenario, no_network, run_all, run_scenario, write_report

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


# ================================================================ "automated evals must run offline"
def test_the_network_is_blocked_while_scenarios_run():
    with no_network():
        with pytest.raises(OfflineViolation):
            socket.create_connection(("example.com", 80))
        with pytest.raises(OfflineViolation):
            socket.socket().connect(("127.0.0.1", 9))
        with pytest.raises(OfflineViolation):
            socket.getaddrinfo("example.com", 80)


def test_a_scenario_that_reaches_for_the_network_fails():
    def sneaky(t):
        t.check("looks fine", True)
        socket.create_connection(("api.openai.com", 443))  # what a real model call would do

    r = run_scenario(Scenario("sneaky", "Test", "must fail", (), "", sneaky))
    assert not r.passed and "network" in (r.error or "")


def test_every_scenario_passes_with_no_credentials_in_the_environment(monkeypatch):
    for k in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL", "LANGSMITH_API_KEY",
              "LANGSMITH_TRACING", "STATE_BACKEND"):
        monkeypatch.delenv(k, raising=False)
    failed = [r.id for r in run_all().values() if not r.passed]
    assert failed == []


def test_the_table_says_the_run_was_offline():
    assert "network access is blocked" in (OUT / "EVAL_TABLE.md").read_text()
