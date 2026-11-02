-- Inferred memories are asked about once.
alter table memories add column if not exists prompted_at timestamptz;
notify pgrst, 'reload schema';