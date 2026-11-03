# Provider table + slot bubbles (for the frontend)

Whenever the agent searches, the chat response carries the **provider table as data**. Render it as rows of
`Provider | Rating | Available slots`, one bubble per slot. The agent's text stays short ("I found 3 options, I'd go with
HomeFlow, pick a slot below"): the table is the display, so never parse slots out of the text.

## Where it comes from
- `POST /chat` response has `options` **only on a turn where a new search ran** (otherwise `null`: keep showing the table you have).
- `GET /conversations/{id}` always includes `options` if the conversation has a search (use it to restore the table when a chat is reopened).
- `POST /conversations/{id}/select` returns the updated `options` too (with `selected` set).

## Shape
```json
{ "kind": "provider_slots", "search_id": "a1b2c3d4e5",
  "request": { "service": "plumber", "area": "Koramangala", "date_text": "Mon 9 Nov to Sun 15 Nov",
               "time_text": "12 pm to 6 pm", "budget": 2000 },
  "relaxed": [],                       // ["time_window"] = nothing fit their window; these are the slots that exist
  "selected": null,                    // {"provider_id","slot_id"} when a card is waiting: turn that bubble orange
  "matches": [
    { "id": "pl_07", "name": "PipeMasters", "rating": 4.8, "price": 1800, "rank": 1, "recommended": true,
      "total_slots": 14,
      "slots": [ { "id": "pl_07_1109_14", "day": "Mon 9 Nov", "label": "2–4 pm",
                   "start": "2026-11-09T14:00:00+05:30", "end": "2026-11-09T16:00:00+05:30", "in_window": true } ] } ] }
```
- **ALL** available slots in the user's dates are included (up to 40 per provider, earliest first; `total_slots` is the real count). Group by `day`.
- Times are already human text (`day`, `label`). `start`/`end` are ISO for sorting only. Never show 24-hour times.
- `price` is the listed service price. Taxes and fees appear only on the approval card.
- `in_window: false` = outside the time window the user asked for (only happens when `relaxed` is set, or after "show me all slots"). Dim or tag these.
- When `relaxed` is non-empty, show a one-line note like "Nothing free after 6 pm. Here's what's available." (the agent's text says it too).

## Selecting (one tap, no second confirmation)
`POST /conversations/{id}/select` `{ "user_id", "provider_id", "slot_id" }` -> the usual chat result:
- success: `approval_card` (the checkout card), `state: "AWAIT_APPROVAL"`, `options.selected` set. Turn the bubble orange. The tap is NOT a booking: the user still taps Approve on the card.
- slot just taken / list out of date: `approval_card: null`, `reply` says so, and `options` is a refreshed table.
- Tapping another bubble replaces the card (the previous slot is released). Messages for the tap are added to the history automatically.
- 404-style cases come back as `exit_reason: "not_found"` (not theirs / deleted) or `busy`.

Typing still works ("book the 4 pm one"): the agent proposes the same card. A bubble is just the faster path.
