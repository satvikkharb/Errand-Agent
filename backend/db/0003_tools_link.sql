-- Link bookings to the hold/key that created them; make the simulator configurable.
alter table scenario_config
  add column if not exists price_drift_prob real not null default 0.3,
  add column if not exists reschedule_conflict_slots text[] not null default '{}',
  add column if not exists forced_book_outcome text;  -- e.g. 'timeout_fail,success' (tests only)

-- Saturday 2026-11-07 morning slot of ac_01 always conflicts on reschedule.
update scenario_config set reschedule_conflict_slots = array['ac_01_1107_09'] where scenario = 'default';

alter table bookings
  add column if not exists hold_id text,
  add column if not exists idempotency_key text;
create unique index if not exists bookings_idem_uq on bookings(idempotency_key) where idempotency_key is not null;
create index if not exists bookings_hold_idx on bookings(hold_id);
