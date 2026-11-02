from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Errand Agent")


class ChatIn(BaseModel):
    user_id: str
    message: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat")
def chat(body: ChatIn):
    # Agent wiring comes in the next step.
    return {"reply": "not implemented yet"}
