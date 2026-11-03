# Errand Agent

A chat agent that arranges home-service errands (plumber, electrician, AC servicing) against mock providers.

**The LLM proposes. Code disposes.** One agent talks to the user; a state machine in plain code owns approval,
idempotency, execution and recovery. The model can never book, cancel or reschedule anything: it can only put an
approval card in front of the user, and only the user's button (verified by code) does the rest.

```
user ─▶ FastAPI ─▶ Orchestrator ─▶ LLM (safe tools only) ─▶ Tool gateway ─▶ mock providers
                       │                                       │ validates, sanitizes, budgets
                       │                       propose_book / cancel / reschedule
                       ▼                                       ▼
              Postgres state ◀──────────  approval card (hash-bound, single-use)
                       ▲                                       │ user presses Approve
                       └───────  Executor (no LLM) ◀───────────┘  idem key · write-ahead log · verify
```

## What is where

| Requirement | Where | Guarded by |
|---|---|---|
| Clarify missing details, compare providers | `app/agent/frame.py`, `gateway.py` (`update_request_frame`, `search_providers`) | evals `ambiguous-time-…`, `no-results-never-relaxes` |
| Approval enforced in code | `app/agent/approvals.py`, `executor.py` (hash-bound, single-use, atomic `begin_attempt`) | evals `adversarial-no-card-no-booking`, mutation `approval-not-single-use` |
| Bounded execution, clear outcome on every exit | `app/agent/budget.py` (12 tool calls, 8 turns, 45 s) | eval `tool-call-limit-ends-cleanly` |
| Idempotent bookings, timeout → check status | `executor.py` (`idem_<approval_id>`, `get_booking_status`, restart recovery) | evals `timeout-after-success`, `timeout-without-booking-retries-same-key`, mutation `claims-success-unverified` |
| Untrusted tool output (prompt injection) | `app/agent/sanitize.py`, gateway | evals `injection-*`, mutation `sanitizer-off` |
| Persistent memory: explicit vs inferred, inspect / correct / forget | `memory.py`, `memory_writer.py` (an LLM pass decides what to remember; code validates it) | evals `forget-removes-it-everywhere`, `inferred-guess-hidden-until-confirmed` |
| Observability | trace spans per run (`/runs/{id}/trace`) and LangSmith (`telemetry.py`) | `tests/test_telemetry.py` |
| ≥ 20 offline evals | `backend/evals/` (**61 scenarios**) | `evals/results/EVAL_TABLE.md` (generated) |
| Proactive follow-up on provider cancellation | `app/agent/proactive.py` (events table, claim compare-and-set) | `tests/test_proactive.py` |
| Context under a fixed token budget | `app/agent/context.py`, `summarizer.py` | eval `context-budget-55-memories-60-turn-chat` |

## Mocks

`backend/app/tools/` is the simulated world: 19 providers, ~1,400 two-hour slots (2–16 Nov 2026), reviews that
include injected instructions, and the eight tools the assignment names (`search_providers`, `get_provider`,
`hold_slot`, `book`, `get_booking_status`, `cancel`, `reschedule`, `list_bookings`).

How a booking behaves is **not a setting**: it depends on the weekday of the slot you pick, so demos are repeatable.

| Day of the slot | What happens |
|---|---|
| Mon | happy path |
| Tue | `book` times out **but the booking went through** (the agent must verify, not retry blindly) |
| Wed | the slot is taken at the last moment |
| Thu | the checkout total is ₹250 higher than quoted |
| Fri | timeout, nothing booked; the retry succeeds |
| Sat | rescheduling into Saturday always conflicts |
| Sun | the provider never confirms |

The simulated clock starts Mon 2 Nov 2026 10:00 IST and runs in real time. Details in `backend/docs/demo-days.md`.

## Run it

**Backend only, no database** (in-memory, seeded; needs just an OpenAI key):

```bash
cd backend
cp .env.example .env            # set OPENAI_API_KEY
uv sync
uv run uvicorn app.main:app --port 8010
```

**With persistence (Supabase / Postgres):**

1. Create a project (pick the region nearest you; it cannot be changed later).
2. Fill `.env`: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_DB_URL`
   (Connect → Session pooler; percent-encode `@` and `#` in the password), and `STATE_BACKEND=supabase`.
3. Apply the schema and seed: `for f in db/0*.sql; do psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f "$f"; done`
4. Start the backend as above. It talks to Postgres through a pooled direct connection (`DB_ACCESS=postgres`, the
   default). Set `DB_ACCESS=rest` to use Supabase's REST API instead.

**Frontend** (Next.js, proxies `/api/be/*` to `BACKEND_URL`):

```bash
cd frontend && npm install
BACKEND_URL=http://localhost:8010 npm run dev -- -p 3100
```

`scripts/rebuild.sh` restarts both for local testing (frontend 3100, backend 8010).

**Tracing (optional):** set `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY` and `LANGSMITH_PROJECT`. Every run becomes one
trace with each model call, tool call and state change as a step. See `backend/docs/langsmith.md`.

## Tests and evals

```bash
cd backend
uv run pytest                       # 400+ offline tests, no network, no keys needed
uv run python -m evals              # 61 scenarios, network blocked; writes evals/results/EVAL_TABLE.md
RUN_DB_TESTS=1 uv run pytest        # also runs the real-database tests (needs the Supabase .env)
```

- `evals/results/EVAL_TABLE.md` is generated by the test run: scenario · expected · actual · pass/fail · notes.
- `evals/results/MUTATION_CHECK.md` proves the evals have teeth: 15 realistic bugs are injected one at a time and each
  must make at least one scenario fail (15/15 are caught).
- The model is scripted in tests and evals, so they are deterministic and free, and the harness blocks the network
  while scenarios run (an eval that reaches for OpenAI, Supabase or LangSmith fails). Real-model behaviour is covered by
  `backend/scripts/live_*.py`.

## API

`POST /chat` · `POST /conversations/{id}/select` (tap a slot, no model call) · `POST /approvals/{id}/approve|reject` ·
`GET /conversations`, `GET|PATCH|DELETE /conversations/{id}` · `GET /bookings` · `GET /memories`,
`POST /memories/{id}/confirm|reject`, `PUT|DELETE /memories/{id}` · `GET /notifications` ·
`GET /runs/{id}/trace` · `POST /internal/tick` (background follow-ups) · `GET /health`.
Payload shapes: `backend/docs/options-api.md`, `approval-card.md`, `history-api.md`.

## Layout

```
backend/app/agent/     orchestrator, gateway (safe tools), approvals, executor, memory, proactive, telemetry
backend/app/tools/     mock providers and the simulated world
backend/db/            SQL migrations 0001-0009 (schema, seed, atomic functions)
backend/evals/         scenario harness, mutation check, generated results
backend/tests/         unit, contract and real-database tests
backend/docs/          API and demo notes
frontend/              Next.js chat UI with the approval card and provider table
```

## Known limits

- Each user message makes one extra small model call to decide what to remember.
- On serverless hosts the last LangSmith batch can be lost when the process freezes; the per-user lock and approval
  path could move to Redis for lower latency.
- The proactive tick needs a scheduler (`POST /internal/tick`); a daily cron is the minimum on Vercel's free tier.
