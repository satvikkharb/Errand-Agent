---
description: Rebuild and restart the frontend (:3100) and backend (:8010) for local testing
argument-hint: "[--be | --fe] [--check] [--fe-port=N] [--be-port=N]"
allowed-tools: Bash(bash scripts/rebuild.sh:*), Bash(curl:*), Bash(tail:*)
---

Rebuild both servers by running the project's rebuild script from the repo root, passing along any
arguments the user gave:

```
bash scripts/rebuild.sh $ARGUMENTS
```

The script restarts the FastAPI backend on port 8010 and the Next.js frontend on port 3100, waits for
both to be healthy, and checks that the frontend can reach the backend through its `/api/be` proxy.
It only stops servers it recognises as its own, and leaves anything else on those ports alone.

Then report briefly:

- whether each server came up, and the two URLs
- if the script failed, the reason it printed, plus the last lines of `.rebuild/backend.log` or
  `.rebuild/frontend.log` for whichever one failed
- if a port was held by another program, say so and suggest `--fe-port=` / `--be-port=` (do not
  kill that program)

Options: `--be` or `--fe` rebuilds only one; `--check` lints and typechecks the frontend first and
aborts on errors. The backend runs with in-memory state by default (no database is touched); set
`STATE_BACKEND=supabase` in the environment to override.
