# Conversation history API (for the frontend)

All calls identify the user with `user_id` (query for GET/DELETE, body for POST/PATCH). Times are ISO-8601.
History lives in Supabase: it survives restarts and works across devices/servers.

## Sidebar list
`GET /conversations?user_id=u&limit=30&before=<last_message_at>`
```json
{ "conversations": [{
    "id": "uuid", "title": "Plumber for a kitchen tap", "title_source": "llm",   // extractive | llm | user | system
    "state": "AWAIT_APPROVAL", "created_at": "...", "last_message_at": "...",
    "message_count": 4, "preview": "Here's what I'd book...", "has_pending_approval": true }] }
```
- Newest activity first. Page with `before` = the last item's `last_message_at`. `limit` is 1-100.
- Empty and deleted conversations are never returned. `title` is `"New conversation"` until the user says something real.
- Show a badge on rows with `has_pending_approval`.

## Open a conversation
`GET /conversations/{id}?user_id=u` -> `{ "conversation": {...same fields...}, "messages": [{id, seq, role, content, created_at}], "approval_card": {...} | null }`
- `role` is `user` or `assistant`. If `approval_card` is set, render the Approve / Cancel card (see /chat).
- 404 if it isn't theirs or was deleted.

## Start / continue a chat
`POST /chat {user_id, message, session_id?}`. Omit `session_id` to start a NEW conversation; the reply contains
`session_id` and `title`. Add it to the top of the sidebar immediately; the title is already final in the reply.
Use the same `session_id` for every later message in that conversation. A deleted/unknown id returns `exit_reason: "not_found"`.

## Rename / delete
- `PATCH /conversations/{id}` `{user_id, title}` -> `{ok, ...conversation}`. 422 if empty, over 80 chars, or unsafe. 404 not theirs. 409 busy: retry.
  A title the user sets is final: the app never overwrites it.
- `DELETE /conversations/{id}?user_id=u` -> `{ok: true}`. Hides it for good and withdraws any waiting approval card. 404 if not theirs / already gone. 409 busy: retry.

## Titles
Instant from the first real message ("Find me a plumber for my kitchen tap tomorrow…"), then replaced in the same
reply by a short model-written title ("Plumber for a kitchen tap") on the first exchange. Follow-ups created by the
system (e.g. a provider cancelled) arrive titled, with `title_source: "system"`.
