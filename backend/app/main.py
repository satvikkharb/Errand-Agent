from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.container import get_orchestrator

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
