"""Proactive follow-up: provider cancels -> agent notices unprompted -> tells the user -> replacement
within known preferences -> FRESH approval before anything is rebooked."""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.agent.proactive import choose_slot, decide, ladder
from app.clock import IST
from app.config import Settings
from app.llm import call, say
from app.tools.sim import provider_cancels
from tests.test_orchestrator import H, NOW

DAY = "1105"


def world(**kw):
    """bk_004 = ac_01 (Indiranagar), Thu 5 Nov 14:00, Rs 899. Make the replacement candidates' slots predictable."""
    h = H(forced_book_outcomes=("success",), **kw)
    for p in ("ac_02", "ac_03"):
        for hh in ("09", "11", "14", "16"):
            h.store.set_slot_status(f"{p}_{DAY}_{hh}", "free")
    return h


def mem(h, key, value, kind="explicit", status="active"):
    h.app.add_memory({"user_id": "user_demo", "key": key, "value": value, "kind": kind, "status": status,
                      "created_at": NOW, "updated_at": NOW})


def block(h, provider, day=DAY, hours=("09", "11", "14", "16")):
    for hh in hours:
        h.store.set_slot_status(f"{provider}_{day}_{hh}", "booked")


def notified(h, **kw):
    provider_cancels(h.store, kw.pop("booking", "bk_004"))
    res = h.o.tick()
    assert res and res[0]["outcome"] == "notified", res
    n = h.app.notifications("user_demo")[-1]
    return n, h.app.session(n["session_id"])


# ================================================================ detection and notification
def test_cancellation_is_detected_and_user_notified_without_any_user_message_or_model_call():
    h = world()
    provider_cancels(h.store, "bk_004")
    assert len(h.store.pending_events()) == 1 and h.app.notifications("user_demo") == []
    res = h.o.tick()  # no user message anywhere
    assert [r["outcome"] for r in res] == ["notified"]
    n = h.app.notifications("user_demo")[0]
    assert "CoolBreeze AC Care cancelled your ac servicing" in n["body"] and "Nothing is booked yet" in n["body"]
    assert n["urgency"] == "normal" and n["read_at"] is None and n["event_id"]
    s = h.app.session(n["session_id"])
    f = s["request_frame"]
    assert (f["service"], f["area"], f["date_start"]) == ("ac_servicing", "Indiranagar", "2026-11-05")
    assert f["_exclude"] == ["ac_01"] and f["_suggestion"]["provider_id"] == "ac_02"  # ac_03 is over budget
    assert h.llm.calls == []  # the loop is code + gateway, no model needed
    assert [m["content"] for m in h.app.messages(s["id"])] == [n["body"]]
    ev = h.store.events()[0]
    assert ev["status"] == "done" and ev["run_id"]
    run = h.app.run(ev["run_id"])
    assert run["trigger"] == "event" and run["status"] == "done"
    names = [(x["kind"], x["name"]) for x in h.app.spans(run["id"])]
    assert ("event", "provider_cancelled") in names and ("tool_call", "search_providers") in names


def test_nothing_is_booked_held_or_approved_by_the_proactive_step():
    h = world()
    n, s = notified(h)
    assert h.store.booking("bk_004")["status"] == "cancelled_by_provider"
    assert len(h.bookings()) == 5 and not h.attempts()
    assert h.app._approvals == {} and h.store._holds == {} and s["pending_approval_id"] is None
    assert h.store.slot(s["request_frame"]["_suggestion"]["slot_id"])["status"] == "free"


# ================================================================ decide whether / how to notify
def test_ignored_events_make_no_noise():
    h = world()
    h.store.add_event("provider_cancelled", "bk_999", {"user_id": "user_demo"}, "e1")        # unknown booking
    h.store.add_event("provider_cancelled", "bk_005", {"user_id": "user_demo"}, "e2")        # still confirmed
    h.store.update_booking("bk_003", status="cancelled_by_provider")                          # but already in the past
    h.store.add_event("provider_cancelled", "bk_003", {"user_id": "user_demo"}, "e3")
    h.store.add_event("something_else", "bk_004", {"user_id": "user_demo"}, "e4")
    reasons = {r["reason"] for r in h.o.tick()}
    assert reasons == {"unknown_booking", "booking_not_cancelled_by_provider", "already_past", "unsupported_event"}
    assert h.app.notifications("user_demo") == [] and all(e["status"] == "ignored" for e in h.store.events())


def test_user_who_already_cancelled_is_not_bothered():
    h = world(cancel_first_503=False)
    h.o.tools_config = h.cfg = type(h.cfg)(**{**h.cfg.__dict__, "cancel_first_503": False})
    h.o._deps("user_demo", None).tools.cfg = h.cfg
    h.store.update_booking("bk_004", status="cancelled")  # the user cancelled it themselves first
    assert provider_cancels(h.store, "bk_004") is None  # the world only cancels confirmed bookings
    h.store.add_event("provider_cancelled", "bk_004", {"user_id": "user_demo"}, "late")  # a late/duplicate event
    assert h.o.tick()[0]["reason"] == "booking_not_cancelled_by_provider"
    assert h.app.notifications("user_demo") == []


def test_urgency_within_24h_and_normal_otherwise():
    h = world()
    h.store.add_booking({"id": "bk_soon", "user_id": "user_demo", "provider_id": "ac_01", "slot_id": None,
                         "service": "ac_servicing", "slot_start": datetime(2026, 11, 3, 9, tzinfo=IST),
                         "status": "confirmed", "price": 899, "hold_id": None, "idempotency_key": None,
                         "created_at": NOW})
    n, _ = notified(h, booking="bk_soon")
    assert n["urgency"] == "urgent" and n["body"].startswith("Urgent: ")
    n2, _ = notified(h, booking="bk_004")
    assert n2["urgency"] == "normal" and not n2["body"].startswith("Urgent")


def test_already_rebooked_same_day_notifies_but_does_not_chase_a_replacement():
    h = world()
    h.store.add_booking({"id": "bk_other", "user_id": "user_demo", "provider_id": "ac_02", "slot_id": None,
                         "service": "ac_servicing", "slot_start": datetime(2026, 11, 5, 11, tzinfo=IST),
                         "status": "confirmed", "price": 749, "hold_id": None, "idempotency_key": None,
                         "created_at": NOW})
    n, s = notified(h)
    assert "already have another ac servicing booked that day" in n["body"]
    assert "_suggestion" not in s["request_frame"] and h.o.open_proposal("user_demo", s["id"]).approval_card is None


def test_decide_is_a_pure_policy():
    b = {"id": "b", "status": "cancelled_by_provider", "service": "plumber", "provider_id": "p",
         "slot_start": NOW + timedelta(hours=30)}
    assert decide({"type": "provider_cancelled"}, b, [], NOW).urgency == "normal"
    assert decide({"type": "provider_cancelled"}, {**b, "slot_start": NOW + timedelta(hours=5)}, [], NOW).urgency == "urgent"
    assert decide({"type": "provider_cancelled"}, {**b, "slot_start": NOW - timedelta(hours=1)}, [], NOW).reason == "already_past"


# ================================================================ replacement within known preferences
def test_saved_preferences_shape_the_replacement():
    h = world()
    mem(h, "avoid_provider", "ac_02")
    mem(h, "default_budget_ac", 1300)
    _, s = notified(h)
    assert s["request_frame"]["_suggestion"]["provider_id"] == "ac_03"  # ac_02 avoided, ac_03 now within budget

    h = world()
    mem(h, "earliest_time", "15:00")
    _, s = notified(h)
    assert s["request_frame"]["_suggestion"]["slot_id"] == f"ac_02_{DAY}_16"  # the 14:00 slot is before 15:00

    h = world()
    mem(h, "default_budget_ac", 1300)
    mem(h, "preferred_ac_servicing", "ac_02")
    _, s = notified(h)
    assert s["request_frame"]["_suggestion"]["provider_id"] == "ac_02"  # preferred beats the higher-rated ac_03

    h = world()
    mem(h, "avoid_provider", "ac_02", status="pending_confirmation", kind="inferred")
    _, s = notified(h)
    assert s["request_frame"]["_suggestion"]["provider_id"] == "ac_02"  # unconfirmed guesses are not preferences


def test_replacement_is_closest_to_the_original_time_and_never_the_provider_who_cancelled():
    h = world()
    _, s = notified(h)
    sug = s["request_frame"]["_suggestion"]
    assert sug["provider_id"] != "ac_01" and sug["slot_id"].endswith("_14") and sug["rung"] == 1


def test_search_widens_in_steps_then_gives_up_honestly():
    h = world()
    block(h, "ac_02"), block(h, "ac_03")           # nothing left that day near or far
    h.store.set_slot_status("ac_02_1106_09", "free")  # but tomorrow-ish there is one
    _, s = notified(h)
    sug = s["request_frame"]["_suggestion"]
    assert sug["slot_id"] == "ac_02_1106_09" and sug["rung"] == 3

    h = world()
    for p in ("ac_02", "ac_03"):
        for d in ("1103", "1104", "1105", "1106", "1107", "1108"):
            block(h, p, d)
    n, s = notified(h)
    assert "couldn't find a replacement" in n["body"] and "_suggestion" not in s["request_frame"]
    f = s["request_frame"]
    assert f["service"] == "ac_servicing" and f["area"] == "Indiranagar"  # the user can continue in chat, no re-asking
    assert s["pending_approval_id"] is None


# ================================================================ fresh approval
def test_opening_the_suggestion_creates_a_fresh_hold_and_approval_then_one_tap_books_it():
    h = world()
    n, s = notified(h)
    assert h.store._holds == {}
    r = h.o.open_proposal("user_demo", s["id"])
    card = r.approval_card
    assert card and r.state == "AWAIT_APPROVAL" and "₹749" in " ".join(card["lines"])
    a = h.app.approval(card["approval_id"])
    assert a["status"] == "pending" and a["payload"]["provider_id"] == "ac_02" and len(h.store._holds) == 1
    assert h.o.open_proposal("user_demo", s["id"]).approval_card["approval_id"] == card["approval_id"]  # idempotent
    assert len(h.store._holds) == 1
    out = h.o.approve("user_demo", card["approval_id"])
    assert out.reply.startswith("Booked and verified")
    new = [b for b in h.bookings() if b["provider_id"] == "ac_02" and b["status"] == "confirmed"]
    assert len(new) == 1 and h.store.booking("bk_004")["status"] == "cancelled_by_provider"
    assert len(h.attempts()) == 1


def test_an_old_approval_can_never_be_reused_for_the_replacement():
    h = world()
    _, s = notified(h)
    first = h.o.open_proposal("user_demo", s["id"]).approval_card["approval_id"]
    h.clock.advance(minutes=11)  # the user comes back late
    second = h.o.open_proposal("user_demo", s["id"]).approval_card["approval_id"]
    assert second != first
    assert h.app.approval(first)["status"] == "invalidated"  # superseded the moment the fresh card was made
    assert h.o.approve("user_demo", first).reply.startswith("That card is out of date")
    assert len(h.bookings()) == 5  # nothing booked by the stale one
    assert h.o.approve("user_demo", second).reply.startswith("Booked")


def test_replacement_taken_while_the_user_was_away_falls_back_or_says_so():
    h = world()
    _, s = notified(h)
    h.store.set_slot_status(s["request_frame"]["_suggestion"]["slot_id"], "booked")  # someone grabbed it
    card = h.o.open_proposal("user_demo", s["id"]).approval_card
    assert card and h.app.approval(card["approval_id"])["payload"]["slot_id"] != s["request_frame"]["_suggestion"]["slot_id"]

    h = world()
    _, s = notified(h)
    block(h, "ac_02"), block(h, "ac_03")
    for d in ("1103", "1104", "1106", "1107", "1108"):
        block(h, "ac_02", d), block(h, "ac_03", d)
    r = h.o.open_proposal("user_demo", s["id"])
    assert r.approval_card is None and "gone" in r.reply and len(h.bookings()) == 5


def test_proposal_stops_if_the_final_price_is_absurdly_above_the_listed_one():
    h = world(price_drift_prob=1.0, price_drift=900)
    _, s = notified(h)
    r = h.o.open_proposal("user_demo", s["id"])
    assert r.approval_card is None and "far above the listed" in r.reply
    assert all(x["status"] == "released" for x in h.store._holds.values())  # priced, then let go
    assert h.store.slot(s["request_frame"]["_suggestion"]["slot_id"])["status"] == "free"
    assert h.app.approval is not None and not h.app._approvals


def test_only_the_owner_can_open_a_proposal():
    h = world()
    _, s = notified(h)
    assert h.o.open_proposal("user_other", s["id"]).exit_reason == "not_found"
    assert h.o.open_proposal("user_demo", "nope").exit_reason == "not_found"


# ================================================================ dedupe, locks, retries
def test_duplicate_events_and_reprocessing_make_one_notification():
    h = world()
    provider_cancels(h.store, "bk_004")
    assert provider_cancels(h.store, "bk_004") is None  # world: already cancelled
    h.o.tick(), h.o.tick()
    assert len(h.app.notifications("user_demo")) == 1
    ev = h.store.events()[0]
    h.store.update_event(ev["id"], status="pending")  # replay the very same event (e.g. at-least-once delivery)
    assert h.o.tick()[0]["outcome"] == "duplicate"
    assert len(h.app.notifications("user_demo")) == 1 and len(h.app._sessions) == 1


def test_busy_user_keeps_the_event_for_the_next_tick_and_other_approvals_survive():
    h = world()
    aid = h.to_card()  # the user has an unrelated plumber approval pending
    provider_cancels(h.store, "bk_004")
    h.app.acquire_lock("user_demo", NOW)
    assert h.o.tick() == [] and len(h.store.pending_events()) == 1  # mid-request: wait
    h.app.release_lock("user_demo")
    assert h.o.tick()[0]["outcome"] == "notified"
    assert h.app.approval(aid)["status"] == "pending"  # untouched
    assert h.o.approve("user_demo", aid).reply.startswith("Booked")


def test_failed_processing_is_retried_then_dropped_after_three_tries(monkeypatch):
    h = world()
    provider_cancels(h.store, "bk_004")
    real = h.o.proactive._notify
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("boom")
        return real(*a, **k)
    monkeypatch.setattr(h.o.proactive, "_notify", flaky)
    assert h.o.tick()[0]["outcome"] == "error" and h.store.events()[0]["status"] == "pending"
    assert h.o.tick()[0]["outcome"] == "notified" and len(h.app.notifications("user_demo")) == 1

    h2 = world()
    provider_cancels(h2.store, "bk_004")
    monkeypatch.setattr(h2.o.proactive, "_notify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("always")))
    for _ in range(3):
        h2.o.tick()
    assert h2.store.events()[0]["status"] == "ignored" and h2.store.events()[0]["payload"]["ignored_reason"] == "processing_failed"
    assert h2.o.tick() == [] and h2.app.notifications("user_demo") == []


def test_event_left_processing_by_a_crash_is_requeued():
    h = world()
    provider_cancels(h.store, "bk_004")
    ev = h.store.pending_events()[0]
    assert h.store.claim_event(ev["id"], NOW) is True and h.store.claim_event(ev["id"], NOW) is False
    assert h.o.tick() == []                       # claimed by a (now dead) worker, not yet stale
    h.clock.advance(minutes=6)
    assert h.o.tick()[0]["outcome"] == "notified"  # stale claim returned to the queue


# ================================================================ through the chat
def test_user_opening_the_chat_gets_the_notice_and_the_model_knows_about_the_suggestion():
    h = world()
    provider_cancels(h.store, "bk_004")
    r = h.chat("hi", say("Hello!"))  # the user just opens the app and says hi
    assert len(r.notices) == 1 and "cancelled your ac servicing" in r.notices[0]
    n = h.app.notifications("user_demo")[0]
    assert n["session_id"] != r.session_id  # the follow-up lives in its own session


def test_chat_in_the_notification_session_can_only_propose_never_book():
    h = world()
    n, s = notified(h)
    sug = s["request_frame"]["_suggestion"]
    h.llm.steps.append(call("propose_book", provider_id=sug["provider_id"], slot_id=sug["slot_id"]))
    r = h.o.handle_message("user_demo", "yes please book that one", s["id"])
    sent = "\n".join(m["content"] for m in h.llm.calls[-1]["messages"])
    assert "Suggested replacement (not proposed yet)" in sent and sug["slot_id"] in sent
    assert r.approval_card and len(h.bookings()) == 5  # a card, not a booking
    assert h.o.approve("user_demo", r.approval_card["approval_id"]).reply.startswith("Booked")


# ================================================================ helpers + API
def test_ladder_and_choose_slot_helpers():
    start = datetime(2026, 11, 5, 14, tzinfo=IST)
    rungs = ladder(start, start + timedelta(hours=2), {"earliest_time": "13:00"})
    assert rungs[0] == ("2026-11-05", "2026-11-05", "13:00", "18:00")
    assert rungs[-1][1] == "2026-11-08" and len(rungs) == 3
    frame = {"_search": {"slots": {"p": ["s1", "s2"]}, "names": {"p": "P"}, "prices": {"p": 5},
                         "times": {"s1": "2026-11-05T09:00:00+05:30", "s2": "2026-11-05T15:00:00+05:30"}}}
    assert choose_slot(frame, start.isoformat())["slot_id"] == "s2"
    assert choose_slot(frame, start.isoformat(), prefer=("p", "s1"))["slot_id"] == "s1"
    assert choose_slot({}, start.isoformat()) is None


def test_api_flow_simulate_notify_open_approve(monkeypatch):
    import app.main as main
    h = world()
    monkeypatch.setattr(main, "get_orchestrator", lambda: h.o)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(dev_endpoints=True, cron_secret="s3cret"))
    c = TestClient(main.app)
    sim = c.post("/dev/simulate/provider-cancel", json={"booking_id": "bk_004"}).json()
    assert sim["processed"][0]["outcome"] == "notified"
    assert c.post("/dev/simulate/provider-cancel", json={"booking_id": "bk_004"}).status_code == 409
    notes = c.get("/notifications", params={"user_id": "user_demo", "unread_only": True}).json()["notifications"]
    assert len(notes) == 1 and c.get("/notifications", params={"user_id": "user_other"}).json()["notifications"] == []
    sid = notes[0]["session_id"]
    assert c.get(f"/sessions/{sid}", params={"user_id": "user_demo"}).json()["approval_card"] is None
    prop = c.post(f"/sessions/{sid}/proposal", json={"user_id": "user_demo"}).json()
    assert prop["approval_card"] and prop["state"] == "AWAIT_APPROVAL"
    assert c.get(f"/sessions/{sid}", params={"user_id": "user_demo"}).json()["approval_card"]
    done = c.post(f"/approvals/{prop['approval_card']['approval_id']}/approve", json={"user_id": "user_demo"}).json()
    assert done["reply"].startswith("Booked and verified")
    assert c.post(f"/notifications/{notes[0]['id']}/read", json={"user_id": "user_demo"}).status_code == 200
    assert c.get("/notifications", params={"user_id": "user_demo", "unread_only": True}).json()["notifications"] == []
    assert c.post(f"/notifications/{notes[0]['id']}/read", json={"user_id": "user_other"}).status_code == 404


def test_api_guards_on_cron_and_dev_endpoints(monkeypatch):
    import app.main as main
    h = world()
    monkeypatch.setattr(main, "get_orchestrator", lambda: h.o)
    c = TestClient(main.app)
    monkeypatch.setattr(main, "get_settings", lambda: Settings(dev_endpoints=False, cron_secret=""))
    assert c.post("/dev/simulate/provider-cancel", json={"booking_id": "bk_004"}).status_code == 404
    assert c.post("/internal/tick").status_code == 503  # no secret configured: not open to the world
    monkeypatch.setattr(main, "get_settings", lambda: Settings(cron_secret="s3cret"))
    assert c.post("/internal/tick").status_code == 401
    assert c.post("/internal/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/internal/tick", headers={"Authorization": "Bearer s3cret"}).json() == {"processed": []}
