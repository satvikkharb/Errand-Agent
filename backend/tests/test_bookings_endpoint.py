"""GET /bookings: what is really booked. The app's "my bookings" list reads this, so it can never show a booking
the system does not have (or miss one it does)."""
from fastapi.testclient import TestClient

from app.llm import call
from tests.test_orchestrator import H


def test_lists_upcoming_confirmed_bookings_with_the_time_range():
    h = H()
    rows = h.o.my_bookings("user_demo")
    by_id = {r["id"]: r for r in rows}
    assert {"bk_004", "bk_005"} <= set(by_id)
    ac = by_id["bk_004"]
    assert ac["service"] == "ac servicing" and ac["status"] == "confirmed" and ac["provider_name"]
    assert "5 Nov" in ac["label"] and "–" in ac["label"]  # "Thu 5 Nov, 2–4 pm": the whole slot, not just its start
    assert [r["start"] for r in rows] == sorted(r["start"] for r in rows)  # soonest first
    assert all(r["start"] > h.clock.now().isoformat() for r in rows)  # nothing in the past


def test_a_new_booking_appears_and_a_cancelled_one_disappears():
    h = H(forced_book_outcomes=("success",))
    before = {r["id"] for r in h.o.my_bookings("user_demo")}
    h.o.approve("user_demo", h.to_card())
    after = {r["id"] for r in h.o.my_bookings("user_demo")}
    assert len(after - before) == 1

    r = h.chat("cancel my plumber booking", call("list_bookings"), call("propose_cancel", booking_id="bk_005"))
    h.o.approve("user_demo", r.approval_card["approval_id"])
    assert "bk_005" not in {x["id"] for x in h.o.my_bookings("user_demo")}


def test_other_people_see_nothing_of_yours():
    h = H()
    assert h.o.my_bookings("user_other") == []


def test_http(monkeypatch):
    import app.main as main
    h = H()
    monkeypatch.setattr(main, "get_orchestrator", lambda: h.o)
    c = TestClient(main.app)
    out = c.get("/bookings", params={"user_id": "user_demo"}).json()
    assert {"bk_004", "bk_005"} <= {b["id"] for b in out["bookings"]}
    assert c.get("/bookings", params={"user_id": "nobody"}).json() == {"bookings": []}
    assert c.get("/bookings").status_code == 422  # user_id is required
