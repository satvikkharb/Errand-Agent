-- Errand Agent schema. Service-role only (RLS on, no policies).
create extension if not exists pgcrypto;

-- ===== core =====
create table users (
  id text primary key,
  name text not null,
  created_at timestamptz not null default now()
);

create table sessions (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references users(id),
  state text not null default 'INTAKE'
    check (state in ('INTAKE','SEARCH','PICK','HOLD','AWAIT_APPROVAL','EXECUTE','RECONCILE','DONE','FAILED')),
  request_frame jsonb not null default '{}',
  active_hold_id text,
  pending_approval_id uuid,
  version int not null default 0,
  locked_until timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index on sessions(user_id);

create table messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  seq int not null,
  role text not null check (role in ('user','assistant','tool','system')),
  content text not null,
  run_id uuid,
  created_at timestamptz not null default now(),
  unique (session_id, seq)
);

create table conversation_summaries (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  covers_up_to_seq int not null,
  text text not null,
  created_at timestamptz not null default now(),
  invalidated_at timestamptz
);

create table runs (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  trigger text not null check (trigger in ('user_message','event','restart_recovery')),
  status text not null default 'running' check (status in ('running','done','failed')),
  turns int not null default 0,
  tool_calls int not null default 0,
  deadline_at timestamptz not null,
  exit_reason text check (exit_reason in ('done','limit_turns','limit_tools','timeout','error')),
  final_message text,
  started_at timestamptz not null default now(),
  ended_at timestamptz
);
create index on runs(session_id);

create table approvals (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references sessions(id) on delete cascade,
  action text not null check (action in ('book','cancel','reschedule')),
  payload jsonb not null,
  payload_hash text not null,
  status text not null default 'pending'
    check (status in ('pending','approved','consumed','rejected','expired','invalidated')),
  expires_at timestamptz not null,
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  consumed_at timestamptz
);
create index on approvals(session_id, status);

create table booking_attempts (
  id uuid primary key default gen_random_uuid(),
  approval_id uuid not null unique references approvals(id),
  action text not null check (action in ('book','cancel','reschedule')),
  idempotency_key text not null unique,
  status text not null default 'started'
    check (status in ('started','succeeded','failed','unknown','reconciled')),
  hold_id text,
  booking_id text,
  tool_response jsonb,
  retry_count int not null default 0,
  started_at timestamptz not null default now(),
  resolved_at timestamptz
);
create index on booking_attempts(status);

create table memories (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references users(id),
  key text not null,
  value jsonb,
  kind text not null check (kind in ('explicit','inferred')),
  status text not null default 'active' check (status in ('active','pending_confirmation','forgotten')),
  confidence real,
  source_message_id uuid,
  superseded_by uuid references memories(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  forgotten_at timestamptz
);
create index on memories(user_id, status);

create table events (
  id uuid primary key default gen_random_uuid(),
  type text not null,
  booking_id text,
  payload jsonb not null default '{}',
  dedupe_key text not null unique,
  status text not null default 'pending' check (status in ('pending','processing','done','ignored')),
  created_at timestamptz not null default now(),
  processed_at timestamptz,
  run_id uuid
);

create table notifications (
  id uuid primary key default gen_random_uuid(),
  user_id text not null references users(id),
  session_id uuid references sessions(id),
  event_id uuid references events(id),
  body text not null,
  created_at timestamptz not null default now(),
  read_at timestamptz
);

create table trace_spans (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references runs(id) on delete cascade,
  parent_id uuid,
  seq int not null,
  kind text not null check (kind in ('state_transition','model_call','tool_call','context','approval')),
  name text not null,
  input jsonb,
  output jsonb,
  latency_ms int,
  tokens_in int,
  tokens_out int,
  cost_usd numeric(10,6),
  error text,
  ts timestamptz not null default now()
);
create index on trace_spans(run_id, seq);

-- ===== simulated provider world =====
create table providers (
  id text primary key,
  name text not null,
  service text not null,
  area text not null,
  rating numeric(2,1) not null,
  base_price int not null,
  attributes jsonb not null default '{}'
);
create index on providers(service, area);

create table slots (
  id text primary key,
  provider_id text not null references providers(id),
  start_at timestamptz not null,
  end_at timestamptz not null,
  status text not null default 'free' check (status in ('free','held','booked'))
);
create index on slots(provider_id, start_at);

create table reviews (
  id uuid primary key default gen_random_uuid(),
  provider_id text not null references providers(id),
  author text not null,
  text text not null
);
create index on reviews(provider_id);

create table holds (
  id text primary key,
  provider_id text not null references providers(id),
  slot_id text not null references slots(id),
  quoted_price int not null,
  expires_at timestamptz not null,
  status text not null default 'active' check (status in ('active','expired','booked','released'))
);

create table bookings (
  id text primary key,
  user_id text not null references users(id),
  provider_id text not null references providers(id),
  slot_id text references slots(id),
  service text not null,
  slot_start timestamptz not null,
  status text not null check (status in ('confirmed','cancelled','completed','cancelled_by_provider')),
  price int not null,
  created_at timestamptz not null default now()
);
create index on bookings(user_id);

create table idempotency_keys (
  key text primary key,
  tool text not null,
  response jsonb not null,
  created_at timestamptz not null default now()
);

create table scenario_config (
  scenario text primary key,
  seed int not null,
  book_outcomes jsonb not null,
  price_drift int not null default 0,
  cancel_first_503 boolean not null default true,
  hold_minutes int not null default 10
);

-- ===== atomic helpers (PostgREST has no multi-statement tx) =====
-- Consume an approved approval and write the attempt row in ONE transaction.
create or replace function begin_attempt(p_approval_id uuid, p_hash text, p_hold_id text)
returns booking_attempts language plpgsql as $$
declare a approvals; att booking_attempts;
begin
  update approvals set status='consumed', consumed_at=now()
   where id=p_approval_id and status='approved' and payload_hash=p_hash and expires_at>now()
   returning * into a;
  if not found then raise exception 'approval_not_consumable'; end if;
  insert into booking_attempts(approval_id, action, idempotency_key, hold_id)
   values (a.id, a.action, 'idem_'||a.id, p_hold_id) returning * into att;
  return att;
end $$;

-- RLS on everything; no policies => only service role can read/write.
do $$ declare t text; begin
  for t in select tablename from pg_tables where schemaname='public' loop
    execute format('alter table %I enable row level security', t);
  end loop; end $$;
