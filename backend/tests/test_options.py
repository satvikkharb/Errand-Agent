"""The provider table: every match with ALL its slots, honest about the time window, and a tap goes straight to a card."""
import json
from datetime import datetime

from fastapi.testclient import TestClient

from app.agent.options import build_options
from app.clock import IST
from app.llm import say
from tests.test_gateway import World
from tests.test_orchestrator import H

NEXT_WEEK = dict(service="plumber", area="Koramangala", date_start="2026-11-09", date_end="2026-11-15", budget_max=2000)


def table(w, **over):
    out = w.call("update_request_frame", **{**NEXT_WEEK, **over}).model_view
    return out["search"], build_options(w.sess()["request_frame"])


def free_in(w, provider_ids, hours, d0="2026-11-09", d1="2026-11-15"):
    s0, s1 = datetime.fromisoformat(f"{d0}T00:00+05:30"), datetime.fromisoformat(f"{d1}T23:59+05:30")
    return sum(1 for s in w.store._slots.values() if s["provider_id"] in provider_ids and s["status"] == "free"
               and s0 <= s["start_at"] <= s1 and s["start_at"].astimezone(IST).hour in hours)


# ================================================================ the calendar behind it
def test_next_week_has_slots_across_the_whole_week_and_in_the_evening():
    w = World()
    search, opts = table(w, window_start="12:00", window_end="18:00")
    days = {s["day"] for p in opts["matches"] for s in p["slots"]}
    assert len(days) >= 5  # a real week, not one day
    assert opts["matches"] and all(s["time"] in ("2–4 pm", "4–6 pm") for p in opts["matches"] for s in p["slots"])
    evening = w.call("update_request_frame", clear=["window"], window_start="18:00").model_view["search"]  # "after 6 pm"
    assert evening["status"] == "ok" and "relaxed" not in evening  # a real request now: nothing had to be relaxed
    assert evening["matches"] and all("6–8 pm" in sl["label"] for m in evening["matches"] for sl in m["slots"])


# ================================================================ the table has EVERY slot
def test_the_user_table_lists_every_available_slot_in_the_period():
    w = World()
    search, opts = table(w, window_start="12:00", window_end="18:00")
    ids = {p["id"] for p in opts["matches"]}
    assert len(opts["matches"]) == 3 and ids == {"pl_05", "pl_06", "pl_07"}  # every Koramangala plumber within budget
    assert sum(len(p["slots"]) for p in opts["matches"]) == free_in(w, ids, hours=(14, 16))  # nothing dropped
    assert all(len(p["slots"]) == p["total_slots"] for p in opts["matches"])
    # the model's own view is trimmed to keep its context small, and says so
    m = search["matches"][0]
    assert len(m["slots"]) <= 8 and m["total_slots"] >= len(m["slots"]) and m["days_available"] and search["table_shown_to_user"]


def test_options_rows_carry_what_the_table_needs():
    w = World()
    _, opts = table(w, window_start="12:00", window_end="18:00")
    assert opts["kind"] == "provider_slots" and opts["selected"] is None and opts["relaxed"] == []
    assert opts["request"] == {"service": "plumber", "area": "Koramangala", "date_text": "Mon 9 Nov to Sun 15 Nov",
                               "time_text": "12 pm to 6 pm", "budget": 2000}
    top = opts["matches"][0]
    assert (top["rank"], top["recommended"]) == (1, True) and top["rating"] >= opts["matches"][1]["rating"]
    s = top["slots"][0]
    assert {"id", "label", "day", "time", "start", "end", "in_window"} == set(s) and s["in_window"] is True
    assert s["label"] == f"{s['day']}, {s['time']}" and s["day"].count(" ") == 2 and "14:00" not in json.dumps(s["label"])
    assert top["visit_fee"] is not None and opts["title"] == "Plumber in Koramangala · Mon 9 Nov to Sun 15 Nov, 12 pm to 6 pm"
    assert [r["rank"] for r in opts["matches"]] == [1, 2, 3] and [r["recommended"] for r in opts["matches"]] == [True, False, False]


def test_a_pending_card_marks_the_selected_bubble():
    w = World()
    _, opts = table(w, window_start="12:00", window_end="18:00", date_start="2026-11-03", date_end="2026-11-04")
    p = opts["matches"][0]
    w.call("propose_book", provider_id=p["id"], slot_id=p["slots"][0]["id"])
    chosen = build_options(w.sess()["request_frame"], w.app.approval(w.sess()["pending_approval_id"]))
    assert chosen["selected"] == {"provider_id": p["id"], "slot_id": p["slots"][0]["id"]}


# ================================================================ a window that is too tight (the screenshot)
def test_when_nothing_fits_the_window_the_user_is_shown_what_exists_and_told_so():
    w = World()
    search, opts = table(w, window_start="21:00")  # "after 9 pm": no such slot exists
    assert search["status"] == "ok" and search["relaxed"] == ["time_window"] and "not met" in search["relaxed_note"]
    assert search["requested_time_window"] == "after 9 pm"
    assert opts["relaxed"] == ["time_window"] and opts["matches"]
    assert all(s["in_window"] is False for p in opts["matches"] for s in p["slots"])  # none of them fit, and it says so


def test_show_all_times_lists_every_slot_and_marks_which_fit():
    w = World()
    strict, _ = table(w, window_start="14:00", window_end="16:00")
    assert all(sl["label"].endswith("2–4 pm") for m in strict["matches"] for sl in m["slots"])
    broad = w.call("search_providers", show_all_times=True).model_view
    opts = build_options(w.sess()["request_frame"])
    labels = {s["time"] for p in opts["matches"] for s in p["slots"]}
    assert labels == {"9–11 am", "11 am–1 pm", "2–4 pm", "4–6 pm", "6–8 pm"}
    assert "relaxed" not in broad and broad["showing_every_slot"] and "fit their requested time window" in broad["browse_note"]  # some DO fit
    assert opts["relaxed"] == []
    fit = [s["in_window"] for p in opts["matches"] for s in p["slots"] if s["time"] == "2–4 pm"]
    miss = [s["in_window"] for p in opts["matches"] for s in p["slots"] if s["time"] != "2–4 pm"]
    assert all(fit) and not any(miss)


def test_browsing_never_changes_the_users_request():
    w = World()
    table(w, window_start="14:00", window_end="16:00")
    w.call("search_providers", show_all_times=True)
    f = w.sess()["request_frame"]
    assert (f["window_start"], f["window_end"], f["budget_max"]) == ("14:00", "16:00", 2000)  # nothing was relaxed behind their back


def test_with_no_window_at_all_nothing_is_reported_as_relaxed():
    w = World()
    search, opts = table(w, any_time=True)
    assert "relaxed" not in search and opts["relaxed"] == []


def test_genuinely_no_slots_still_says_no_matches_with_hints():
    w = World()
    w.store._slots = {k: v for k, v in w.store._slots.items() if v["provider_id"] not in ("pl_05", "pl_06", "pl_07")}
    search, opts = table(w, window_start="12:00", window_end="18:00")
    assert search["status"] == "no_matches" and opts is None and "date" in search["suggest_asking_user_to_relax"]


def test_a_slot_outside_the_window_can_be_proposed_only_because_it_was_listed():
    w = World()
    _, opts = table(w, window_start="21:00")
    p = opts["matches"][0]
    assert w.call("propose_book", provider_id=p["id"], slot_id=p["slots"][0]["id"]).ends_turn  # the user picked it from the table
    assert w.call("propose_book", provider_id=p["id"], slot_id="pl_05_1120_09").error == "not_in_search_results"  # not listed


# ================================================================ through the orchestrator
def ask_week(h, *extra):
    from evals.harness import fill
    return h.chat("My plumber in Koramangala next week", fill(area="Koramangala", date_start="2026-11-09", date_end="2026-11-15",
                                                              window_start="12:00", window_end="18:00", budget_max=2000),
                  say("Found three options, I'd go with the top one."), *extra)


def test_the_live_table_rides_on_every_reply_and_says_whether_this_turn_made_it():
    h = H()
    r = ask_week(h)
    assert r.options and len(r.options["matches"]) == 3 and r.options["selected"] is None and r.options["new_this_turn"] is True
    r2 = h.chat("thanks!", say("You're welcome."))
    assert r2.options["search_id"] == r.options["search_id"] and r2.options["new_this_turn"] is False  # "see the table" stays true
    conv = h.o.get_conversation("user_demo", r.session_id)
    assert conv["options"]["search_id"] == r.options["search_id"]  # reopening a chat restores it


def test_tapping_a_slot_goes_straight_to_the_card_without_the_model():
    h = H()
    r = ask_week(h)
    calls = len(h.llm.calls)
    p, s = r.options["matches"][1], r.options["matches"][1]["slots"][2]
    out = h.o.select_slot("user_demo", r.session_id, p["id"], s["id"])
    assert len(h.llm.calls) == calls  # no model involved
    assert out.approval_card and out.state == "AWAIT_APPROVAL" and out.options["selected"] == {"provider_id": p["id"], "slot_id": s["id"]}
    assert out.approval_card["total"] >= p["price"]
    msgs = [m["content"] for m in h.app.messages(r.session_id)]
    assert msgs[-2].startswith(f"I'll take {p['name']}") and "Approve" in msgs[-1]  # the chat history reads naturally
    assert len(h.bookings()) == 5  # selecting is not booking


def test_tapping_another_slot_replaces_the_card_and_frees_the_first_slot():
    h = H()
    r = ask_week(h)
    a = h.o.select_slot("user_demo", r.session_id, r.options["matches"][0]["id"], r.options["matches"][0]["slots"][0]["id"])
    first_slot = r.options["matches"][0]["slots"][0]["id"]
    assert h.store.slot(first_slot)["status"] == "held"
    b = h.o.select_slot("user_demo", r.session_id, r.options["matches"][1]["id"], r.options["matches"][1]["slots"][0]["id"])
    assert h.app.approval(a.approval_card["approval_id"])["status"] == "invalidated" and h.store.slot(first_slot)["status"] == "free"
    assert b.approval_card["approval_id"] != a.approval_card["approval_id"]


def test_a_slot_that_just_went_gives_a_refreshed_table_not_an_error():
    h = H()
    r = ask_week(h)
    p, s = r.options["matches"][0], r.options["matches"][0]["slots"][0]
    h.store.set_slot_status(s["id"], "booked")  # someone else got it a moment ago
    out = h.o.select_slot("user_demo", r.session_id, p["id"], s["id"])
    assert out.approval_card is None and "no longer available" in out.reply
    assert s["id"] not in {x["id"] for pr in out.options["matches"] for x in pr["slots"]}  # the new table drops it


def test_selection_is_checked_like_everything_else():
    h = H()
    r = ask_week(h)
    p, s = r.options["matches"][0], r.options["matches"][0]["slots"][0]
    assert h.o.select_slot("user_other", r.session_id, p["id"], s["id"]).exit_reason == "not_found"
    assert h.o.select_slot("user_demo", "nope", p["id"], s["id"]).exit_reason == "not_found"
    out = h.o.select_slot("user_demo", r.session_id, "pl_04", "pl_04_1109_09")  # never offered
    assert out.approval_card is None and "no longer available" in out.reply
    h.app.acquire_lock("user_demo", h.clock.now())
    assert h.o.select_slot("user_demo", r.session_id, p["id"], s["id"]).busy
    h.app.release_lock("user_demo")
    h.o.delete_conversation("user_demo", r.session_id)
    assert h.o.select_slot("user_demo", r.session_id, p["id"], s["id"]).exit_reason == "not_found"


def test_tapped_card_books_on_approve_and_the_receipt_uses_the_chosen_slot():
    h = H(forced_book_outcomes=("success",))
    r = ask_week(h)
    p, s = r.options["matches"][0], r.options["matches"][0]["slots"][1]
    card = h.o.select_slot("user_demo", r.session_id, p["id"], s["id"]).approval_card
    done = h.o.approve("user_demo", card["approval_id"])
    b = h.bookings()[-1]
    assert done.reply.startswith("Booked and verified") and (b["provider_id"], b["slot_id"]) == (p["id"], s["id"])


# ================================================================ HTTP
def test_http_select_and_restore(monkeypatch):
    import app.main as main
    h = H()
    monkeypatch.setattr(main, "get_orchestrator", lambda: h.o)
    c = TestClient(main.app)
    r = ask_week(h)
    sid, p, s = r.session_id, r.options["matches"][0], r.options["matches"][0]["slots"][0]
    got = c.get(f"/conversations/{sid}", params={"user_id": "user_demo"}).json()
    assert got["options"]["matches"][0]["slots"][0]["id"] == s["id"]
    out = c.post(f"/conversations/{sid}/select", json={"user_id": "user_demo", "provider_id": p["id"], "slot_id": s["id"]}).json()
    assert out["approval_card"]["action"] == "book" and out["options"]["selected"]["slot_id"] == s["id"]
    again = c.get(f"/conversations/{sid}", params={"user_id": "user_demo"}).json()
    assert again["options"]["selected"]["slot_id"] == s["id"] and again["approval_card"]["approval_id"] == out["approval_card"]["approval_id"]
    assert c.post(f"/conversations/{sid}/select", json={"user_id": "user_demo", "provider_id": "", "slot_id": "x"}).status_code == 422


def test_browsing_with_a_window_nothing_fits_is_reported_as_relaxed():
    w = World()
    table(w, window_start="21:00")  # nothing fits "after 9 pm"
    broad = w.call("search_providers", show_all_times=True).model_view
    assert broad["relaxed"] == ["time_window"] and "showing_every_slot" not in broad
