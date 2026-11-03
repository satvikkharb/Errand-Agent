"""Approval lifecycle, enforced in code. The model can only *propose*; nothing here reads model text.

An approval binds the exact action: provider, slot, price, booking. Any change => new approval.
"""
import hashlib
import json
from datetime import datetime

from app.agent.deps import Deps
from app.agent.timefmt import price_breakdown, when


def payload_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _fmt(iso: str, end_iso: str | None = None) -> str:
    return when(iso, end_iso)  # "Wed 4 Nov, 2-4 pm", never "14:00"


def render_card(a: dict) -> dict:
    """The thing the user sees and taps. Built from stored data, never from model text."""
    p = a["payload"]
    extra: dict = {}
    if a["action"] == "book":
        # Chat only ever quotes the service price. Taxes and fees appear here, at checkout, and nowhere else.
        items = price_breakdown(p["search_price"], p["price"])
        lines = [f"{p['provider_name']} ({p['service'].replace('_', ' ')})", _fmt(p["slot_start"], p.get("slot_end"))]
        lines += [f"{i['label']} ₹{i['amount']}" for i in items] if len(items) > 1 else []
        lines.append(f"Total ₹{p['price']}")
        extra = {"breakdown": items, "total": p["price"]}
        title = "Confirm booking"
    elif a["action"] == "cancel":
        lines = [f"{p['provider_name']} ({p['service'].replace('_', ' ')})", _fmt(p["slot_start"]), f"₹{p['price']}"]
        title = "Confirm cancellation"
    else:
        lines = [f"{p['provider_name']} ({p['service'].replace('_', ' ')})",
                 f"From {_fmt(p['from_start'])}", f"To {_fmt(p['to_start'])}"]
        title = "Confirm reschedule"
    return {"approval_id": a["id"], "action": a["action"], "title": title, "lines": lines,
            "expires_at": a["expires_at"].isoformat(), "buttons": ["Approve", "Cancel"], **extra}


def invalidate_pending(deps: Deps, session_id: str, reason: str) -> str | None:
    """Void the session's open approval and release its hold. Returns the voided approval id."""
    s = deps.app.session(session_id)
    aid = s["pending_approval_id"]
    voided = None
    if aid:
        a = deps.app.approval(aid)
        if a and a["status"] in ("pending", "approved"):
            deps.app.update_approval(aid, status="invalidated", decided_at=deps.clock.now())
            voided = aid
            deps.tracer.span("approval", "invalidated", input={"approval_id": aid, "reason": reason})
            hold_id = a["payload"].get("hold_id")
            if hold_id:
                release_hold(deps, hold_id)
    if s["pending_approval_id"] or s["active_hold_id"]:
        deps.app.update_session(session_id, pending_approval_id=None, active_hold_id=None)
    return voided


def release_hold(deps: Deps, hold_id: str) -> None:
    h = deps.store.hold(hold_id)
    if h and h["status"] == "active":
        deps.store.update_hold(hold_id, status="released")
        deps.store.claim_slot(h["slot_id"], "held", "free")


def create_pending(deps: Deps, session_id: str, action: str, payload: dict, expires_at: datetime) -> dict:
    invalidate_pending(deps, session_id, reason="superseded")
    a = deps.app.add_approval({
        "session_id": session_id, "action": action, "payload": payload,
        "payload_hash": payload_hash(payload), "status": "pending",
        "expires_at": expires_at, "created_at": deps.clock.now()})
    deps.app.update_session(session_id, pending_approval_id=a["id"], active_hold_id=payload.get("hold_id"))
    deps.tracer.span("approval", "created", input={"approval_id": a["id"], "action": action,
                                                   "hash": a["payload_hash"][:12]})
    return a


def approve(deps: Deps, approval_id: str, user_id: str) -> dict:
    """The Approve button. Returns {"ok": True, "approval": ...} or {"ok": False, "error": ...}."""
    a = deps.app.approval(approval_id)
    s = deps.app.session(a["session_id"]) if a else None
    if not a or s["user_id"] != user_id:
        return {"ok": False, "error": "not_found"}
    if a["status"] != "pending":
        return {"ok": False, "error": f"approval_{a['status']}"}
    now = deps.clock.now()
    if now >= a["expires_at"]:
        deps.app.update_approval(approval_id, status="expired", decided_at=now)
        if a["payload"].get("hold_id"):
            release_hold(deps, a["payload"]["hold_id"])  # don't leave the slot locked until a later sweep
        return {"ok": False, "error": "approval_expired"}
    if payload_hash(a["payload"]) != a["payload_hash"]:
        deps.app.update_approval(approval_id, status="invalidated", decided_at=now)
        return {"ok": False, "error": "approval_tampered"}
    if a["action"] == "book":
        h = deps.store.hold(a["payload"]["hold_id"])
        if not h or h["status"] != "active" or now >= h["expires_at"]:
            deps.app.update_approval(approval_id, status="expired", decided_at=now)
            release_hold(deps, a["payload"]["hold_id"])
            return {"ok": False, "error": "hold_expired"}
    deps.app.update_approval(approval_id, status="approved", decided_at=now)
    deps.tracer.span("approval", "approved", input={"approval_id": approval_id, "action": a["action"]})
    return {"ok": True, "approval": deps.app.approval(approval_id)}


def reject(deps: Deps, approval_id: str, user_id: str) -> dict:
    a = deps.app.approval(approval_id)
    s = deps.app.session(a["session_id"]) if a else None
    if not a or s["user_id"] != user_id:
        return {"ok": False, "error": "not_found"}
    if a["status"] != "pending":
        return {"ok": False, "error": f"approval_{a['status']}"}
    deps.app.update_approval(approval_id, status="rejected", decided_at=deps.clock.now())
    if a["payload"].get("hold_id"):
        release_hold(deps, a["payload"]["hold_id"])
    deps.app.update_session(a["session_id"], pending_approval_id=None, active_hold_id=None)
    deps.tracer.span("approval", "rejected", input={"approval_id": approval_id})
    return {"ok": True}
