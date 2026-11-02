"""Seeds the in-memory app store like db/0002_seed.sql: 55 memories + a 60-message prior conversation."""
from datetime import datetime

from app.agent.app_store import InMemoryAppStore

SEED_SESSION_ID = "00000000-0000-0000-0000-000000000001"
_NOTES = ["call before arriving", "no shoe covers needed", "pet dog at home", "prefers invoice by email",
          "wifi password not shared", "parking on street", "building has lift", "avoid lunch hour 1-2pm"]
_USER = ["I need an electrician for a fan regulator.", "Anything after 10 works.", "Is Amit available Thursday?",
         "Budget is around 1300.", "Yes go ahead.", "Also my AC needs servicing next month."]
_BOT = ["Which area are you in?", "Here are 3 options with prices and ratings.", "Amit has a 10-12 slot. Shall I hold it?",
        "Held at Rs 1250. Please confirm to book.", "Booked and verified.", "Noted. I will remind you."]


def seed_demo(app: InMemoryAppStore, now: datetime, user_id: str = "user_demo") -> None:
    def mem(key, value, kind="explicit", status="active", conf=1.0):
        app.add_memory({"user_id": user_id, "key": key, "value": value, "kind": kind, "status": status,
                        "confidence": conf, "created_at": now, "updated_at": now})

    for k, v in [("preferred_area", "Indiranagar"), ("earliest_time", "09:00"), ("preferred_electrician", "el_02"),
                 ("preferred_plumber", "pl_01"), ("avoid_provider", "pl_04"), ("default_budget_plumber", 1500),
                 ("default_budget_ac", 1000), ("language", "English"),
                 ("home_type", "3BHK apartment, 4th floor"), ("weekend_ok", True)]:
        mem(k, v)
    for k, v, c in [("preferred_time_of_day", "morning", 0.7), ("prefers_cheapest", True, 0.55),
                    ("likes_high_rating", True, 0.6), ("prefers_weekdays", True, 0.5)]:
        mem(k, v, "inferred", "pending_confirmation", c)
    mem("prefers_same_provider", True, "inferred", "active", 0.9)  # inferred but confirmed by the user
    for k in ("old_area", "phone_note", "gate_code"):
        mem(k, None, status="forgotten")
    for n in range(1, 38):
        mem(f"note_{n}", f"Low-relevance preference #{n} (e.g. {_NOTES[n % 8]})", conf=0.8)

    app.create_session(user_id, state="DONE", session_id=SEED_SESSION_ID)
    for n in range(1, 61):
        if n % 2:
            app.add_message(SEED_SESSION_ID, "user", f"Earlier user turn {n}: {_USER[n % 6]}")
        else:
            app.add_message(SEED_SESSION_ID, "assistant", f"Earlier assistant turn {n}: {_BOT[n % 6]}")
    app.add_summary(user_id, "User booked electrician Amit Electricals (el_02) in Indiranagar for a fan "
                             "regulator, Rs 1250. Mentioned AC servicing next month.", SEED_SESSION_ID, 60)
