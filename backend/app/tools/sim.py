"""Simulator hooks. Not exposed to the LLM: used by evals and the demo to trigger world events."""
from app.tools.store import Store


def provider_cancels(store: Store, booking_id: str) -> dict | None:
    """A provider cancels a confirmed booking. Emits one deduped event for the agent to find.

    The slot stays unavailable (the provider is gone for that window).
    Returns None if the booking isn't confirmed (already cancelled, unknown).
    """
    b = store.booking(booking_id)
    if not b or b["status"] != "confirmed":
        return None
    store.update_booking(booking_id, status="cancelled_by_provider")
    inserted = store.add_event(
        "provider_cancelled", booking_id,
        {"provider_id": b["provider_id"], "service": b["service"], "slot_start": b["slot_start"].isoformat(),
         "user_id": b["user_id"]},
        dedupe_key=f"provider_cancelled:{booking_id}",
    )
    return {"event_inserted": inserted, "booking_id": booking_id}
