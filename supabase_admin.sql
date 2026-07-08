-- ============================================================================
-- CoatFlow — Admin Dashboard: platform-adminrol op app_users
-- ----------------------------------------------------------------------------
-- Draai dit ÉÉNMALIG in de Supabase SQL-editor (Project → SQL → New query).
--
-- Voegt een platform-brede adminvlag toe. Dit is NIET de per-bedrijf 'owner'-rol
-- (elke geregistreerde schilder is owner van zijn eigen company); is_admin = een
-- platformbeheerder die het /admin-dashboard mag zien (alle tenants).
--
-- Het /admin-dashboard leest deze vlag SERVER-SIDE via de service_role key en
-- queryt cross-tenant. Zonder is_admin = true krijgt niemand toegang.
-- ============================================================================

-- 1) Adminvlag — default false, dus bestaande gebruikers zijn géén admin.
alter table public.app_users
    add column if not exists is_admin boolean not null default false;

-- 2) (Optioneel) index voor snelle adminlookup.
create index if not exists idx_app_users_is_admin
    on public.app_users(is_admin) where is_admin = true;

-- 3) Wijs de platformbeheerder(s) aan. PAS HET E-MAILADRES AAN naar je eigen
--    beheerdersaccount. Alleen accounts die je hier expliciet zet krijgen toegang.
update public.app_users
   set is_admin = true
 where lower(email) = lower('renzodomen2009@gmail.com');

-- 4) Controle: wie is admin?
-- select id, email, role, is_admin, company_id from public.app_users where is_admin;

-- ============================================================================
-- BEVEILIGING
-- ----------------------------------------------------------------------------
-- * RLS op app_users blijft staan (tenant_app_users-policy). Het admin-dashboard
--   gebruikt de service_role key (db._get_client), die RLS bewust bypasst — dit
--   is enkel toegankelijk via de server-side admin-guard in de Python-app.
-- * De anon/JWT-client (gewone app) kan is_admin van andere bedrijven NIET lezen
--   (RLS isoleert op company_id). Een gewone gebruiker kan zichzelf dus niet
--   tot admin promoveren via de app.
-- ============================================================================
