"""An approval is the authority to act, not the conversation's current state: tapping Approve on a card that is
still valid must work even if the chat has moved on since the card was shown."""
import pytest

from app.agent import states
from app.llm import call
from tests.test_orchestrator import H


@pytest.mark.parametrize("moved_to", [states.SEARCH, states.INTAKE, states.PICK])
def test_cancel_works_on_the_first_tap_even_if_the_conversation_moved_on(moved_to):
    h = H()
    r = h.chat("cancel my plumber booking", call("list_bookings"), call("propose_cancel", booking_id="bk_005"))
    assert r.approval_card and r.state == states.AWAIT_APPROVAL
    h.app.update_session(r.session_id, state=moved_to)  # the user kept chatting; the card is still on screen and valid
    out = h.o.approve("user_demo", r.approval_card["approval_id"])  # used to raise InvalidTransition (a 500)
    assert out.reply.startswith("Cancelled and verified") and out.state == states.DONE
    assert h.store.booking("bk_005")["status"] == "cancelled"
    assert h.o.approve("user_demo", r.approval_card["approval_id"]).reply.startswith("That approval was already used")


def test_a_booking_card_also_survives_the_conversation_moving_on():
    h = H(forced_book_outcomes=("success",))
    aid = h.to_card()
    h.app.update_session(h.sid, state=states.SEARCH)
    out = h.o.approve("user_demo", aid)
    assert out.reply.startswith("Booked and verified") and out.state == states.DONE and len(h.bookings()) == 6
