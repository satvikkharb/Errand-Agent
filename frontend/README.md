# Errand frontend

Next.js (App Router) + Tailwind v4, wired to the FastAPI backend in `../backend`.

```
cp .env.local.example .env.local     # BACKEND_URL, default http://localhost:8000
npm install
npm run dev                          # http://localhost:3000
```

Backend (separate terminal): `cd ../backend && uv run uvicorn app.main:app --port 8000`
(needs `OPENAI_API_KEY`; `STATE_BACKEND=memory` runs with seeded in-memory data and no database).

The browser never calls the backend directly. `next.config.ts` proxies `/api/be/*` to `BACKEND_URL`,
because the backend has no CORS middleware.

## Frontend to backend map

| Frontend | Backend | Notes |
| --- | --- | --- |
| Send a chat message | `POST /chat` `{user_id, message, session_id}` | `session_id` is kept from the first reply so the thread continues |
| Reply bubble | `ChatResult.reply` | |
| "How I got there" link | `GET /runs/{run_id}/trace` | model calls, tool calls, state moves |
| Approval card | `ChatResult.approval_card` | `lines` are rendered as-is; the card is built by the backend from stored data |
| "Approve and pay" on a **booking** | Hippopay, then `POST /approvals/{id}/approve` | see below |
| "Approve" on a cancel / reschedule | `POST /approvals/{id}/approve` | no payment step |
| "Not this one" | `POST /approvals/{id}/reject` | |
| Typing "yes" / "no" with a card open | same as the buttons | booking still goes through Hippopay |
| Grey-out of an old card | `GET /sessions/{id}` | on reload, cards the backend no longer holds become "stale" |
| Notices (inline, orange) | `ChatResult.notices` | reconcile and proactive messages |
| Sidebar "Updates" | `GET /notifications?unread_only=true` (polled every 30s) and `POST /notifications/{id}/read` | |
| "Want me to remember X?" buttons | `ChatResult.memory_prompt`, `POST /memories/{id}/confirm` or `/reject` | |
| `/memories` page | `GET /memories`, `POST /memories/{id}/confirm\|reject`, `DELETE /memories/{id}` | |
| Plan limit, Plus plan, `/checkout` | none (frontend only) | |

### Pay first, book second

The backend books on `approve`; it knows nothing about payment. Hippopay therefore *authorises*
the card, calls approve, and only *captures* if the session comes back `DONE`:

- `DONE`: payment done, graffiti, errand added to the sidebar.
- `RECONCILE` / `EXECUTE` (outcome unclear): "payment on hold", the backend resolves it later and
  posts an update.
- anything else (slot taken, expired hold, ...): "no charge made" and the backend's own explanation.

### Identity

The backend has no auth and trusts `user_id`. The frontend derives one from the email
(`lib/session.ts`, `userIdFor`), so the same email always lands in the same backend user.

## Layout

- `lib/api.ts`: typed client, one function per endpoint above
- `lib/chat.ts`: chat state (localStorage), turns `ChatResult`s into thread items
- `lib/cards.ts`: reads the backend's card `lines`
- `lib/session.ts`: mock login (localStorage)
- `app/hippopay`: the make-believe payment provider, `components/Graffiti.tsx` for the celebration

## Known gaps (backend side)

- No `GET /bookings`, so "Booked from here" only lists errands booked on this device. A list
  endpoint would let the sidebar show real history.
- No `GET /sessions` list, so the frontend can resume only the session it remembers.
- The backend card has no `provider_id` or structured price, so the frontend parses `lines`.
