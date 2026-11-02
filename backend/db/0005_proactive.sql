-- Proactive follow-up support.
alter table events add column if not exists claimed_at timestamptz;
create index if not exists events_status_idx on events(status, created_at);
alter table notifications add column if not exists urgency text not null default 'normal'
  check (urgency in ('normal', 'urgent'));
create index if not exists notifications_event_idx on notifications(event_id);

-- Allow trace spans for incoming events.
alter table trace_spans drop constraint if exists trace_spans_kind_check;
alter table trace_spans add constraint trace_spans_kind_check
  check (kind in ('state_transition', 'model_call', 'tool_call', 'context', 'approval', 'event'));

notify pgrst, 'reload schema';
