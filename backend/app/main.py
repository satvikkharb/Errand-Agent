from contextlib import contextmanager
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.container import get_orchestrator
from app.tools.sim import provider_cancels

app = FastAPI(title="Errand Agent")


class ChatIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    message: str = Field(max_length=4000)
    session_id: str | None = None


class DecisionIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)


def _orch():
    o = get_orchestrator()
    if o is None:
        raise HTTPException(503, "OPENAI_API_KEY is not set")
    return o


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(body: ChatIn):
    return asdict(_orch().handle_message(body.user_id, body.message, body.session_id))


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, body: DecisionIn):
    return asdict(_orch().approve(body.user_id, approval_id))


@app.post("/approvals/{approval_id}/reject")
def reject(approval_id: str, body: DecisionIn):
    return asdict(_orch().reject(body.user_id, approval_id))


@app.get("/runs/{run_id}/trace")
def trace(run_id: str):
    o = _orch()
    run = o.app.run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return {"run": run, "spans": o.app.spans(run_id)}


@app.get("/sessions/{session_id}/messages")
def messages(session_id: str, user_id: str):
    o = _orch()
    s = o.app.session(session_id)
    if not s or s["user_id"] != user_id:
        raise HTTPException(404, "session not found")
    return {"state": s["state"], "messages": o.app.messages(session_id)}


# ------------------------------------------------------------------ proactive follow-up
class SimCancelIn(BaseModel):
    booking_id: str


@app.get("/notifications")
def notifications(user_id: str, unread_only: bool = False):
    o = _orch()
    o.tick(user_id=user_id)  # pick up anything the world did while the user was away
    return {"notifications": o.app.notifications(user_id, unread_only)}


@app.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str, body: DecisionIn):
    o = _orch()
    if not o.app.mark_notification_read(notification_id, body.user_id, o.clock.now()):
        raise HTTPException(404, "notification not found")
    return {"ok": True}


@app.get("/sessions/{session_id}")
def session(session_id: str, user_id: str):
    o = _orch()
    s = o.app.session(session_id)
    if not s or s["user_id"] != user_id:
        raise HTTPException(404, "session not found")
    return {"state": s["state"], "approval_card": o.card(session_id)}


@app.post("/sessions/{session_id}/proposal")
def open_proposal(session_id: str, body: DecisionIn):
    return asdict(_orch().open_proposal(body.user_id, session_id))


@app.post("/internal/tick")
def tick(authorization: str | None = Header(default=None)):
    secret = get_settings().cron_secret
    if not secret:
        raise HTTPException(503, "CRON_SECRET is not set")
    if authorization != f"Bearer {secret}":
        raise HTTPException(401, "unauthorized")
    return {"processed": _orch().tick()}


@app.post("/dev/simulate/provider-cancel")
def simulate_provider_cancel(body: SimCancelIn):
    if not get_settings().dev_endpoints:
        raise HTTPException(404, "not found")
    o = _orch()
    emitted = provider_cancels(o.store, body.booking_id)
    if emitted is None:
        raise HTTPException(409, "booking is not confirmed")
    return {"emitted": emitted, "processed": o.tick()}


# ------------------------------------------------------------------ memory: inspect, confirm, correct, forget
class CorrectIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    value: Any


@contextmanager
def _locked(o, user_id: str):
    if not o.app.acquire_lock(user_id, o.clock.now(), 60):
        raise HTTPException(409, "busy with another request, try again")
    try:
        yield
    finally:
        o.app.release_lock(user_id)


@app.get("/memories")
def memories(user_id: str):
    return {"memories": _orch().memory.list_for_user(user_id)}


@app.post("/memories/{memory_id}/confirm")
def confirm_memory(memory_id: str, body: DecisionIn):
    o = _orch()
    with _locked(o, body.user_id):
        m = o.memory.confirm(body.user_id, memory_id)
    if not m:
        raise HTTPException(404, "no pending memory with that id")
    return {"ok": True, "memory": {k: m[k] for k in ("id", "key", "value", "kind", "status")}}


@app.post("/memories/{memory_id}/reject")
def reject_memory(memory_id: str, body: DecisionIn):
    o = _orch()
    with _locked(o, body.user_id):
        ok = o.memory.reject(body.user_id, memory_id)
    if not ok:
        raise HTTPException(404, "no pending memory with that id")
    return {"ok": True}


@app.put("/memories/{memory_id}")
def correct_memory(memory_id: str, body: CorrectIn):
    o = _orch()
    with _locked(o, body.user_id):
        status = o.memory.correct(body.user_id, memory_id, body.value)
    if status == "not_found":
        raise HTTPException(404, "memory not found")
    if status in ("bad_value", "unknown_key"):
        raise HTTPException(422, status)
    return {"ok": True, "status": status}


@app.delete("/memories/{memory_id}")
def forget_memory(memory_id: str, user_id: str):
    o = _orch()
    with _locked(o, user_id):
        res = o.memory.forget(user_id, memory_id=memory_id)
    if not res:
        raise HTTPException(404, "memory not found")
    return {"ok": True, **res}
