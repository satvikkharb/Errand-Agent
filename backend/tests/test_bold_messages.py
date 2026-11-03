"""What the customer should be able to spot at a glance (service, provider, date and time) is marked **bold**.
The app renders ** as bold; the model is asked to do the same, and the system's own messages do it deterministically."""
import re

from app.llm import call, say
from tests.test_gateway import World
from tests.test_orchestrator import H

BOLD = re.compile(r"\*\*(.+?)\*\*")


def test_a_booked_message_bolds_service_provider_and_time():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    out = h.o.approve("user_demo", aid)
    service, provider, when = BOLD.findall(out.reply)
    assert out.reply.startswith(f"Booked and verified: **{service}** with **{provider}**, **{when}**")
    assert service == "plumber" and provider and re.search(r"\d", when)  # a real date and time, not a placeholder
    assert "Booking ID" in out.reply  # the rest of the message is unchanged


def test_a_cancelled_message_bolds_service_provider_and_time():
    h = H()
    r = h.chat("cancel my plumber booking", call("list_bookings"), call("propose_cancel", booking_id="bk_005"))
    out = h.o.approve("user_demo", r.approval_card["approval_id"])
    assert out.reply.startswith("Cancelled and verified: **") and len(BOLD.findall(out.reply)) == 3


def test_a_rescheduled_message_bolds_service_provider_and_the_new_time():
    h = H()
    r = h.chat("move my AC servicing to Friday",
               call("get_availability", provider_id="ac_01", date_start="2026-11-06", date_end="2026-11-06"),
               lambda _: call("propose_reschedule", booking_id="bk_004",
                              slot_id=h.app.session(h.sid)["request_frame"]["_avail"]["ac_01"][0]))
    out = h.o.approve("user_demo", r.approval_card["approval_id"])
    service, provider, when = BOLD.findall(out.reply)
    assert service == "ac servicing" and out.reply.startswith("Rescheduled and verified: **")
    assert "now **" in out.reply and "6 Nov" in when


def test_bookings_the_model_reads_carry_a_ready_made_date_and_time():
    w = World()
    rows = w.call("list_bookings").model_view["bookings"]
    assert rows and all(re.search(r"\w{3} \d+ \w{3}, ", b["label"]) for b in rows)  # "Thu 5 Nov, 2 pm"


# ================================================================ the model's own wording
from app.agent.emphasis import emphasize  # noqa: E402


def test_the_reply_from_the_screenshot_gets_its_facts_bolded():
    text = "You have one confirmed booking for a plumber with PipeMasters on Monday, 2nd November, from 6 to 8 pm."
    assert emphasize(text, {"PipeMasters"}) == (
        "You have one confirmed booking for a **plumber** with **PipeMasters** on **Monday, 2nd November**, from **6 to 8 pm**.")


def test_several_bookings_in_one_reply():
    text = ("You have two upcoming confirmed bookings: a plumber with DripStop on Wed 4 Nov at 11 am, and an AC servicing "
            "with CoolBreeze AC Care on Thu 5 Nov at 2 pm.")
    out = emphasize(text, {"DripStop", "CoolBreeze AC Care"})
    assert BOLD.findall(out) == ["plumber", "DripStop", "Wed 4 Nov", "11 am", "AC servicing", "CoolBreeze AC Care",
                                 "Thu 5 Nov", "2 pm"]


def test_slot_labels_and_ranges_are_one_piece_each():
    assert emphasize("Mon 9 Nov, 2–4 pm", set()) == "**Mon 9 Nov**, **2–4 pm**"
    assert emphasize("the 11 am–1 pm slot", set()) == "the **11 am–1 pm** slot"
    assert emphasize("Tue 03 Nov, 6:30 pm", set()) == "**Tue 03 Nov**, **6:30 pm**"


def test_nothing_else_is_touched():
    for plain in ("I may be able to help. It costs ₹1500 and takes about 2 hours.",
                  "A decent option, 2 marketplace listings, and 3 Octopus cards.",
                  "Which one would you like?"):
        assert emphasize(plain, set()) == plain
    assert emphasize("DripStopper is not DripStop-the-provider", {"DripStop"}).count("**") == 2  # whole word only


def test_the_wording_never_changes_and_a_formatted_reply_is_left_alone():
    text = "Booked a plumber with DripStop on Wed 4 Nov at 11 am, then 2–4 pm on Thu 5 Nov."
    assert emphasize(text, {"DripStop"}).replace("**", "") == text
    already = "You have a **plumber** with DripStop on Wed 4 Nov."
    assert emphasize(already, {"DripStop"}) == already


def test_the_orchestrator_applies_it_to_what_the_model_says():
    h = H()
    r = h.chat("what do I have?", call("list_bookings"),
               say("You have an AC servicing with CoolBreeze AC Care on Thu 5 Nov at 2 pm."))
    assert BOLD.findall(r.reply) == ["AC servicing", "CoolBreeze AC Care", "Thu 5 Nov", "2 pm"]


def test_month_first_dates_are_bolded_too():
    assert emphasize("Plumber DripStop on Wed, Nov 4 at 11 am", {"DripStop"}) == \
        "**Plumber** **DripStop** on **Wed, Nov 4** at **11 am**"
    assert emphasize("on Wednesday, November 4th, 2–4 pm", set()) == "on **Wednesday, November 4th**, **2–4 pm**"
    assert emphasize("Nov 9 works", set()) == "**Nov 9** works"
    assert emphasize("Which of the 5 options? I may follow up on May", set()) == "Which of the 5 options? I may follow up on May"
