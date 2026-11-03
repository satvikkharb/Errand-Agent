"""Reschedule times as bubbles: what get_availability puts on screen, and that a tap goes straight to a card."""
from app.agent.options import build_options, options_id
from app.llm import call, say
from tests.test_gateway import World
from tests.test_orchestrator import H

# bk_004: user_demo's AC servicing with ac_01 on Thu 5 Nov (the seeded upcoming booking)
WEEK = dict(provider_id="ac_01", date_start="2026-11-06", date_end="2026-11-07")


def avail(w, **over):
    return w.call("get_availability", **{**WEEK, "booking_id": "bk_004", **over})


# ================================================================ the table
def test_availability_for_a_booking_becomes_a_table_of_bubbles():
    w = World()
    out = avail(w)
    assert out.model_view["table_shown_to_user"] and out.model_view["booking_id"] == "bk_004"
    opts = build_options(w.sess()["request_frame"])
    assert opts["kind"] == "reschedule_slots" and opts["selected"] is None
    assert opts["booking"]["id"] == "bk_004" and "5 Nov" in opts["booking"]["current"]  # where it is now
    (row,) = opts["matches"]
    assert row["id"] == "ac_01" and row["total_slots"] == len(row["slots"]) >= 2
    assert all(s["label"] and s["day"] and s["time"] for s in row["slots"])
    assert [s["start"] for s in row["slots"]] == sorted(s["start"] for s in row["slots"])  # in time order
    # exactly the slots the model was told about, minus none: the table never offers what the rules would refuse
    assert {s["id"] for s in row["slots"]} == {s["id"] for s in out.model_view["slots"]}


def test_the_booking_can_be_left_out_when_there_is_only_one_with_that_provider():
    w = World()
    avail(w, booking_id=None)
    assert build_options(w.sess()["request_frame"])["booking"]["id"] == "bk_004"


def test_no_table_without_a_booking_to_move():
    w = World()
    w.call("get_availability", provider_id="pl_01", date_start="2026-11-06", date_end="2026-11-07")  # no booking with pl_01
    assert build_options(w.sess()["request_frame"]) is None


def test_a_booking_with_another_provider_is_refused():
    w = World()
    assert avail(w, provider_id="pl_01").error == "booking_is_with_another_provider"
    assert avail(w, booking_id="bk_nope").error == "not_found"
    assert build_options(w.sess()["request_frame"]) is None


def test_every_availability_call_is_a_new_table():
    w = World()
    avail(w)
    first = options_id(w.sess()["request_frame"])
    avail(w)
    assert options_id(w.sess()["request_frame"]) != first


def test_a_search_afterwards_puts_the_provider_table_back():
    w = World()
    avail(w)
    w.ready(service="plumber", area="Koramangala", date_start="2026-11-09", date_end="2026-11-15")
    w.call("search_providers")
    assert build_options(w.sess()["request_frame"])["kind"] == "provider_slots"


# ================================================================ through the orchestrator
def ask_to_move(h):
    return h.chat("I want to move my AC servicing", call("get_availability", **WEEK, booking_id="bk_004"),
                  say("Here are the times it has free, pick one below."))


def test_the_turn_returns_the_table_and_a_tap_goes_straight_to_the_reschedule_card():
    h = H()
    r = ask_to_move(h)
    assert r.options and r.options["kind"] == "reschedule_slots" and not r.approval_card
    calls = len(h.llm.calls)
    slot = r.options["matches"][0]["slots"][1]
    out = h.o.select_slot("user_demo", r.session_id, "ac_01", slot["id"])
    assert len(h.llm.calls) == calls  # no model involved
    assert out.approval_card and out.approval_card["action"] == "reschedule" and out.state == "AWAIT_APPROVAL"
    assert out.options["selected"] == {"provider_id": "ac_01", "slot_id": slot["id"]}
    assert slot["label"] in " ".join(out.approval_card["lines"]) or slot["day"] in " ".join(out.approval_card["lines"])
    assert h.store.booking("bk_004")["slot_id"] == "ac_01_1105_14"  # selecting is not rescheduling
    msgs = [m["content"] for m in h.app.messages(r.session_id)]
    assert msgs[-2].startswith("Move ") and "Approve" in msgs[-1]  # the history reads naturally


def test_tapping_another_time_replaces_the_card():
    h = H()
    r = ask_to_move(h)
    a, b = r.options["matches"][0]["slots"][0], r.options["matches"][0]["slots"][1]
    first = h.o.select_slot("user_demo", r.session_id, "ac_01", a["id"])
    second = h.o.select_slot("user_demo", r.session_id, "ac_01", b["id"])
    assert h.app.approval(first.approval_card["approval_id"])["status"] == "invalidated"
    assert second.approval_card["approval_id"] != first.approval_card["approval_id"]
    assert second.options["selected"]["slot_id"] == b["id"]


def test_a_time_that_just_went_gives_a_refreshed_table_not_an_error():
    h = H()
    r = ask_to_move(h)
    gone = r.options["matches"][0]["slots"][0]
    h.store.set_slot_status(gone["id"], "booked")  # someone else got it a moment ago
    out = h.o.select_slot("user_demo", r.session_id, "ac_01", gone["id"])
    assert out.approval_card is None and "no longer available" in out.reply
    assert gone["id"] not in {s["id"] for s in out.options["matches"][0]["slots"]}


def test_only_times_from_the_table_are_accepted():
    h = H()
    r = ask_to_move(h)
    for provider, slot in (("ac_01", "ac_01_1120_09"), ("pl_04", r.options["matches"][0]["slots"][0]["id"])):
        out = h.o.select_slot("user_demo", r.session_id, provider, slot)  # not in the table / wrong provider
        assert out.approval_card is None and "no longer available" in out.reply
    assert h.o.select_slot("user_other", r.session_id, "ac_01", r.options["matches"][0]["slots"][0]["id"]).exit_reason == "not_found"


def test_reopening_the_chat_restores_the_reschedule_table():
    h = H()
    r = ask_to_move(h)
    conv = h.o.get_conversation("user_demo", r.session_id)
    assert conv["options"]["kind"] == "reschedule_slots" and conv["options"]["view_id"] == r.options["view_id"]
    again = h.chat("thanks!", say("You're welcome.")).options  # still the live table, flagged as not new
    assert again["view_id"] == r.options["view_id"] and again["new_this_turn"] is False
