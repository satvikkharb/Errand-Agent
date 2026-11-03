# Demo days: what happens when you book

There is **no setting** for this. What a booking does is decided by **the day it is for**, so a demo is just a
choice of day. Same service, same provider, same request: only the date differs. The simulated "today" is
**Mon 2 Nov 2026** (the clock starts at 10:00 am and runs in real time).

| Say this | Day | What happens | What it shows |
|---|---|---|---|
| "plumber **today** after 2 pm, under 1500" | Mon 2 Nov | Books cleanly at the listed price → "Booked and verified" | the happy flow |
| "plumber **tomorrow**" | Tue 3 Nov | Confirm → the call **times out**, but it did book; the status check finds it → "Booked and verified" | timeout after success, no duplicate |
| "plumber **the day after tomorrow**" | Wed 4 Nov | Confirm → **"that slot was just taken"**, nothing booked, offers to search again | slot conflict |
| "plumber on **Thursday**" | Thu 5 Nov | Card shows **Service ₹X + GST + service fee = Total** (quote is ₹250 higher than listed); books | price differs at checkout |
| "plumber on **Friday**" | Fri 6 Nov | First call **times out and booked nothing**; automatic retry with the same key → books once | timeout, then safe retry |
| "move my AC servicing to **Saturday**" | Sat 7 Nov | **Slot conflict**: the original booking is untouched. (Booking a slot on Saturday works.) | reschedule conflict |
| "plumber on **Sunday**" | Sun 8 Nov | **Never confirms**: every attempt times out; the agent says it cannot confirm and never claims success; resolves when the hold expires (10 min) | unknown outcome handled honestly |
| "plumber on **Monday 9 Nov**" | Mon 9 Nov | Books cleanly | happy again |

Notes
- The day is the day of the **slot you book** (Asia/Kolkata), not today's date. Anything outside 2-9 Nov behaves like the happy flow.
- Every attempt on an edge-case day behaves the same way, so on Wed 4 Nov picking another slot is taken too: pick another day.
- Cancelling is unaffected by the day: the first cancel call fails with a transient 503 and the retry succeeds (the assignment's rule).
- The old `SCENARIO_MODE` switch and the `scenario_config` table are gone: nothing in the environment or the database changes these behaviours.
