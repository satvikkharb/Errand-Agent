"""The saved end-to-end trace must really show a timeout-after-success handled correctly (so the docs cannot drift)."""
import json
from pathlib import Path

TRACE = Path(__file__).resolve().parent.parent / "docs" / "traces" / "timeout-after-success.json"


def test_the_saved_trace_shows_a_timeout_that_was_verified_not_retried():
    d = json.loads(TRACE.read_text())
    approve = d["runs"][-1]
    spans = approve["spans"]
    names = [(s["name"], s.get("error")) for s in spans if s["kind"] == "tool_call"]
    assert names[0] == ("exec.book", "ToolTimeout")  # the side effect timed out ...
    assert names[1][0] == "exec.get_booking_status"  # ... and the very next step asks the source of truth
    assert [n for n, _ in names].count("exec.book") == 1  # no blind retry
    status = next(s for s in spans if s["name"] == "exec.get_booking_status")
    assert status["output"]["status"] == "booked"
    assert not [s for s in spans if s["kind"] == "model_call"] and approve["run"]["turns"] == 0  # no model in the money step
    assert [s["name"] for s in spans if s["kind"] == "state_transition"][-2:] == ["EXECUTE->RECONCILE", "RECONCILE->DONE"]


def test_the_saved_trace_has_exactly_one_booking_one_attempt_and_a_derived_key():
    d = json.loads(TRACE.read_text())
    assert d["proof_one_booking_one_attempt"] == {"bookings": 1, "attempts": 1}
    att, ap = d["attempts"][0], d["approval"]
    assert att["idempotency_key"] == f"idem_{ap['id']}" and att["retry_count"] == 0 and att["status"] == "succeeded"
    assert ap["status"] == "consumed" and d["bookings"][0]["id"] == att["booking_id"]
    assert d["steps"][-1]["reply"].startswith("Booked and verified")


def test_the_trace_is_contiguous_and_every_model_call_has_token_counts():
    d = json.loads(TRACE.read_text())
    for r in d["runs"]:
        assert [s["seq"] for s in r["spans"]] == list(range(len(r["spans"])))
        assert all(s.get("tokens_in") for s in r["spans"] if s["kind"] == "model_call")
