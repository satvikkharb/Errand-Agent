"""Produce the end-to-end trace for a run that includes a TIMEOUT AFTER SUCCESS, from a real run (real OpenAI model).

Run from backend/:  PYTHONPATH=. uv run python scripts/make_trace.py

What it does: a user asks for an electrician, taps a Tuesday slot (Tuesday is the day `book` times out even though the
booking went through), and approves. The executor must NOT retry blindly and must NOT claim success: it asks the
provider's source of truth (get_booking_status) and only then reports "Booked and verified".

Writes docs/traces/timeout-after-success.json (the raw trace: every run, every span, plus the rows that prove there is
exactly one booking). Uses the in-memory world, so it touches no database. LangSmith also receives it if tracing is on.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ["STATE_BACKEND"] = "memory"  # never touch a database from this script

from app.container import get_orchestrator  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "traces" / "timeout-after-success.json"
USER = "trace_demo"
TUESDAY = "2026-11-03"  # the simulated clock starts Mon 2 Nov 2026; Tuesday books time out after succeeding


def main() -> int:
    o = get_orchestrator()
    if o is None:
        print("OPENAI_API_KEY is not set", file=sys.stderr)
        return 1
    steps = []

    def record(label, res):
        steps.append({"step": label, "run_id": res.run_id, "reply": res.reply, "state": res.state,
                      "exit_reason": res.exit_reason, "approval_card": res.approval_card})
        print(f"[{label}] {res.state}: {res.reply[:110]!r}")
        return res

    r = record("user: ask", o.handle_message(USER, "Fan regulator is broken, I need an electrician in Indiranagar "
                                                   "tomorrow evening, under 1500."))
    if not (r.options and r.options.get("matches")):
        r = record("user: show slots", o.handle_message(USER, "Show me all the available slots.", r.session_id))
    pick = next(((m, s) for m in r.options["matches"] for s in m["slots"] if s["start"].startswith(TUESDAY)), None)
    if not pick:
        print("no Tuesday slot offered", file=sys.stderr)
        return 1
    m, s = pick
    r = record(f"user taps: {m['name']} {s['label']}", o.select_slot(USER, r.session_id, m["id"], s["id"]))
    card = r.approval_card
    r = record("user taps Approve", o.approve(USER, card["approval_id"]))

    sid = r.session_id
    runs = [x for x in o.app._runs.values() if x["session_id"] == sid]
    runs.sort(key=lambda x: x["started_at"])
    attempts = [a for a in o.app._attempts.values() if a["approval_id"] == card["approval_id"]]
    doc = {
        "what": "One conversation ending in a booking whose first `book` call TIMED OUT even though it succeeded. "
                "The executor verified with get_booking_status instead of retrying or guessing.",
        "model": "real OpenAI model (see model_call spans); provider world: in-memory mocks; day: Tuesday",
        "steps": steps,
        "runs": [{"run": x, "spans": o.app.spans(x["id"])} for x in runs],
        "approval": o.app.approval(card["approval_id"]),
        "attempts": attempts,
        "bookings": [b for b in o.store.bookings_for_user(USER)],
        "proof_one_booking_one_attempt": {"bookings": len(o.store.bookings_for_user(USER)), "attempts": len(attempts)},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=2, default=lambda v: v.isoformat() if isinstance(v, datetime) else str(v)))
    print(f"\nwrote {OUT} ({OUT.stat().st_size // 1024} KB): {len(runs)} runs, "
          f"{sum(len(r['spans']) for r in doc['runs'])} spans, bookings={doc['proof_one_booking_one_attempt']}")
    if o.telemetry:
        o.telemetry.drain(20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
