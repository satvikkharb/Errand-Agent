-- Conversation history: titles, soft delete, and a one-query sidebar list.
alter table sessions
  add column if not exists title text,
  add column if not exists title_source text check (title_source in ('extractive', 'llm', 'user', 'system')),
  add column if not exists deleted_at timestamptz;

update sessions set title = 'Electrician for a fan regulator', title_source = 'system'
 where id = '00000000-0000-0000-0000-000000000001' and title is null;

-- The sidebar: this user's non-empty, non-deleted conversations, most recently active first.
create or replace function list_conversations(p_user text, p_limit int default 30, p_before timestamptz default null)
returns table (id uuid, title text, title_source text, state text, created_at timestamptz,
               last_message_at timestamptz, message_count bigint, preview text, has_pending_approval boolean)
language sql stable as $$
  select s.id, s.title, s.title_source, s.state, s.created_at, lm.created_at, mc.n, left(lm.content, 140),
         exists (select 1 from approvals a where a.id = s.pending_approval_id and a.status = 'pending')
    from sessions s
    join lateral (select m.created_at, m.content from messages m where m.session_id = s.id
                   order by m.seq desc limit 1) lm on true
    join lateral (select count(*) as n from messages m where m.session_id = s.id) mc on true
   where s.user_id = p_user and s.deleted_at is null and (p_before is null or lm.created_at < p_before)
   order by lm.created_at desc
   limit p_limit
$$;

notify pgrst, 'reload schema';
