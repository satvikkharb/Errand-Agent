-- Supporting pieces for the Supabase-backed app store.
alter table users add column if not exists locked_until timestamptz;
alter table trace_spans add column if not exists extra jsonb;  -- span fields without a dedicated column

-- Every UPDATE on a session bumps its version (optimistic-lock signal) and updated_at.
create or replace function bump_session() returns trigger language plpgsql as $$
begin new.version := old.version + 1; new.updated_at := now(); return new; end $$;
drop trigger if exists sessions_bump on sessions;
create trigger sessions_bump before update on sessions for each row execute function bump_session();

-- Atomic per-user lock. Expires on its own (a crash can never wedge a user). Creates the user on first contact.
create or replace function acquire_user_lock(p_user text, p_now timestamptz, p_until timestamptz)
returns boolean language plpgsql as $$
declare n int;
begin
  insert into users(id, name) values (p_user, p_user) on conflict (id) do nothing;
  update users set locked_until = p_until where id = p_user and (locked_until is null or locked_until <= p_now);
  get diagnostics n = row_count;
  return n = 1;
end $$;

-- Consume an approved approval and write the attempt row in ONE transaction. The caller supplies the clock.
drop function if exists begin_attempt(uuid, text, text);
create or replace function begin_attempt(p_approval_id uuid, p_hash text, p_hold_id text, p_now timestamptz)
returns booking_attempts language plpgsql as $$
declare a approvals; att booking_attempts;
begin
  update approvals set status = 'consumed', consumed_at = p_now
   where id = p_approval_id and status = 'approved' and payload_hash = p_hash and expires_at > p_now
   returning * into a;
  if not found then raise exception 'approval_not_consumable'; end if;
  insert into booking_attempts(approval_id, action, idempotency_key, hold_id, started_at)
   values (a.id, a.action, 'idem_' || a.id, p_hold_id, p_now) returning * into att;
  return att;
end $$;

-- Attempts left 'started' or 'unknown' (crash or unclear outcome). p_user null = everyone.
create or replace function unresolved_attempts(p_user text default null)
returns setof booking_attempts language sql stable as $$
  select b.* from booking_attempts b
    join approvals a on a.id = b.approval_id
    join sessions s on s.id = a.session_id
   where b.status in ('started', 'unknown') and (p_user is null or s.user_id = p_user)
   order by b.started_at
$$;

-- Runs a crashed process left 'running' past their deadline.
create or replace function close_stale_runs(p_now timestamptz) returns int language plpgsql as $$
declare n int;
begin
  update runs set status = 'failed', exit_reason = 'error', ended_at = p_now,
         final_message = coalesce(final_message, 'interrupted')
   where status = 'running' and deadline_at < p_now;
  get diagnostics n = row_count;
  return n;
end $$;

notify pgrst, 'reload schema';
