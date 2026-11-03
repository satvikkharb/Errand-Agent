-- "Today" (2 Nov, the simulated date) gets slots too, so "anytime after 2 pm today" has something to find.
insert into slots(id, provider_id, start_at, end_at, status)
select p.id || '_1102_' || lpad(h::text, 2, '0'), p.id,
       (timestamp '2026-11-02' + make_interval(hours => h)) at time zone 'Asia/Kolkata',
       (timestamp '2026-11-02' + make_interval(hours => h + 2)) at time zone 'Asia/Kolkata',
       case when abs(hashtext(p.id || '2026-11-02' || h::text)) % 5 = 0 then 'booked' else 'free' end
  from providers p cross join (values (9), (11), (14), (16)) as w(h)
on conflict (id) do nothing;
