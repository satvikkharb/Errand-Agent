#!/usr/bin/env bash
# Wipe all data and reload the seed (schema stays). Use after demos or live runs against Supabase.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -q -c "truncate table trace_spans, runs, notifications, events, booking_attempts, approvals, conversation_summaries, messages, sessions, memories, idempotency_keys, holds, bookings, reviews, slots, providers, scenario_config, users cascade"
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -q -f db/0002_seed.sql
psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -q -c "update scenario_config set reschedule_conflict_slots = array['ac_01_1107_09'] where scenario = 'default'"
echo "reset ok: $(psql "$SUPABASE_DB_URL" -Atc "select count(*) from providers") providers, $(psql "$SUPABASE_DB_URL" -Atc "select count(*) from memories") memories, $(psql "$SUPABASE_DB_URL" -Atc "select count(*) from bookings") bookings"
