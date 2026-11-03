# Demo days: what happens when you book

There is **no setting** for this. What a booking does is decided by **the day of the slot**, so a demo is just a
choice of day. Same service, same provider, same request: only the date differs. The cycle **repeats every week**.
The simulated "today" is **Mon 2 Nov 2026** (the clock starts at 10:00 am and runs in real time) and the calendar
runs to **Mon 16 Nov**, with five two-hour windows a day (9-11, 11-1, 2-4, 4-6, **6-8 pm**).

| Say this | Weekday | What happens | What it shows |
|---|---|---|---|
| "plumber **today** after 2 pm" / "next **Monday**" | Monday (2, 9, 16 Nov) | Books cleanly at the listed price | the happy flow |
| "plumber **tomorrow**" / "next **Tuesday**" | Tuesday (3, 10 Nov) | Confirm: the call **times out**, but it did book; the status check finds it → "Booked and verified" | timeout after success, no duplicate |
| "plumber the **day after tomorrow**" / "next **Wednesday**" | Wednesday (4, 11 Nov) | Confirm: **"that slot was just taken"**, nothing booked | slot conflict |
| "plumber on **Thursday**" | Thursday (5, 12 Nov) | Card shows **Service + GST + service fee = Total** (quote ₹250 above listed); books | price differs at checkout |
| "plumber on **Friday**" | Friday (6, 13 Nov) | First call **times out and booked nothing**; automatic retry with the same key books once | timeout, then safe retry |
| "move my AC servicing to **Saturday**" | Saturday (7, 14 Nov) | **Slot conflict**: the original booking is untouched. (Booking a Saturday slot works.) | reschedule conflict |
| "plumber on **Sunday**" | Sunday (8, 15 Nov) | **Never confirms**: every attempt times out; the agent says it can't confirm and never claims success; resolves when the hold expires (10 min) | unknown outcome handled honestly |

Notes
- The day is the day of the **slot you book** (Asia/Kolkata), not today's date.
- Every attempt on an edge-case day behaves the same way, so on a Wednesday picking another slot is taken too: pick another day.
- Cancelling is unaffected by the day: the first cancel call fails with a transient 503 and the retry succeeds (the assignment's rule).
- Nothing in the environment or the database changes these behaviours.

## "Show me the slots"
The agent shows a table of every available slot in the period you gave (see `docs/options-api.md`). If nothing fits your
time window (say "after 9 pm"), it shows what exists, marked as outside your window, and says so. It never changes your request.
