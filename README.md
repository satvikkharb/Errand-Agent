# Errand Agent

Chat agent that arranges home-service errands (plumber, AC servicing, electrician) against mock providers.
The LLM proposes actions. Code owns approval, idempotency and execution.

- `backend/`: FastAPI + Supabase (Python)
- `frontend/`: simple chat input (later)

## Backend
```
cd backend
cp .env.example .env   # fill in keys
uv sync
uv run uvicorn app.main:app --reload
```
