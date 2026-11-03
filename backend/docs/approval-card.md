# Approval card (checkout) shape: changed

Chat only ever quotes the **service price** (e.g. "HomeFlow, ₹1100"). Taxes and fees appear ONLY on the card, which is the checkout screen the user approves.

```json
{
  "approval_id": "uuid", "action": "book", "title": "Confirm booking",
  "lines": ["HomeFlow (plumber)", "Mon 2 Nov, 4–6 pm", "Service ₹1100", "GST (18%) ₹198", "Service fee ₹52", "Total ₹1350"],
  "breakdown": [{"label": "Service", "amount": 1100}, {"label": "GST (18%)", "amount": 198}, {"label": "Service fee", "amount": 52}],
  "total": 1350,
  "expires_at": "...", "buttons": ["Approve", "Cancel"]
}
```
- Render `breakdown` as rows and `total` as the bold final line (or just print `lines`). `breakdown` amounts always add up to `total`.
- With no extra charges, `breakdown` is a single "Service" row and `lines` ends with "Total ₹…" only.
- Cancel/reschedule cards have no `breakdown`/`total`.
- All times are already human text ("Mon 2 Nov, 4–6 pm"). Do not reformat them.
- The booking receipt in chat reads e.g. "Booked and verified: HomeFlow, Mon 2 Nov, 4–6 pm, ₹1350 in total (₹1100 service plus taxes and fees)."
