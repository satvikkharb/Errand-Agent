-- A full second week (so "next week" has slots) and a 6-8 pm window every day (so "after 6 pm" is a real request).
insert into slots(id, provider_id, start_at, end_at, status)
select p.id || '_' || to_char(d, 'MMDD') || '_' || lpad(h::text, 2, '0'), p.id,
       (d + make_interval(hours => h)) at time zone 'Asia/Kolkata',
       (d + make_interval(hours => h + 2)) at time zone 'Asia/Kolkata',
       case when abs(hashtext(p.id || d::text || h::text)) % 5 = 0 then 'booked' else 'free' end
  from providers p
 cross join generate_series(timestamp '2026-11-02', timestamp '2026-11-16', interval '1 day') d
 cross join (values (9), (11), (14), (16), (18)) as w(h)
on conflict (id) do nothing;
