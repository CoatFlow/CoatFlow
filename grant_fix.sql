-- CoatFlow — GRANT-fix: geef service_role/authenticated rechten op de tabellen.
-- Draai dit in de SQL Editor van je DEV-project (vcydxegftiduavbhdipa).
-- Lost 'permission denied for table' (42501) op. Idempotent.
grant usage on schema public to anon, authenticated, service_role;
grant all on all tables    in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
