# LangSmith tracing

Every orchestrator run is **one trace**; every step is a **child run**, in order.

| Root run | When |
|---|---|
| `chat` | a user message |
| `select_slot` | a tap on a slot bubble (no model call) |
| `approve` / `reject` | the user pressed the card button |
| `open_proposal` | the user opened a proactive suggestion |
| `event:provider_cancelled` | the background follow-up |

| Child run | Type | Holds |
|---|---|---|
| `context: context` | chain | tokens, memories used, history dropped |
| `<model-id>` | llm | exact messages sent, reply, tool calls, input/output tokens, est. cost |
| `<tool>` (e.g. `search_providers`) | tool | arguments, sanitized result, latency, errors |
| `exec.book`, `exec.get_booking_status` | tool | executor side effects (timeout, then status check) |
| `state: ...`, `approval: ...`, `event: ...` | chain | workflow steps |

Root runs carry `thread_id` (= conversation id, so the **Threads** view groups a chat), `user_id`,
tags (`outcome:booked`, `exit:limit_tools`, `injection-blocked`) and a score (`booking_verified` 1/0).

Best-effort: no key = off; a dead LangSmith never slows or breaks a reply (sends run in a background thread).
Set `LANGSMITH_SEND_PROMPTS=false` to keep message text out. Our own database never stores prompts.
On serverless hosts a frozen process can drop the last batch; use a long-lived host for full traces.
