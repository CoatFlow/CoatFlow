-- ############################################################################
-- CoatFlow — DEV database setup (ALLES-IN-1)
-- Volgorde: SCHEMA -> RLS -> TRIGGER -> ADMIN. Idempotent.
-- NA het draaien: (A) Auth Hook aanzetten, (B) admin-account registreren + sectie 4 opnieuw.
-- ############################################################################

-- ######## SECTIE 1/4 — SCHEMA (tabellen + basis-RLS + grants) ########
-- ============================================================================
-- CoatFlow — Supabase PostgreSQL schema   (Fase 1: database-fundering)
-- ----------------------------------------------------------------------------
-- Multi-tenant + login-ready + RLS-prepared. Bevat GEEN login/auth/abonnement-
-- logica — alleen de structuur zodat die fases later zonder herbouw kunnen volgen.
--
-- Draai dit éénmalig in de Supabase SQL-editor (Project → SQL → New query).
-- Daarna: migrate_json_to_supabase.py voor de data.
--
-- Tenant-model:  companies (1) ──< klanten / producten / personeel / projecten /
--                taken / agenda_items / offertes / facturen / activiteiten
--                projecten ──< (onderdelen jsonb)  +  M:N personeel via project_personeel
-- Elke business-tabel draagt company_id → harde tenant-isolatie + RLS.
-- Sleutels: PRIMARY KEY (company_id, id) zodat de bestaande integer-id's behouden
--           blijven én uniek zijn binnen een bedrijf.
-- ============================================================================

create extension if not exists "pgcrypto";   -- gen_random_uuid()

-- ============================================================================
-- TENANT ROOT
-- ============================================================================
create table if not exists companies (
    id                   uuid primary key default gen_random_uuid(),
    naam                 text        not null default 'Mijn bedrijf',
    -- Bedrijfsinstellingen (de 76 keys uit 'instellingen') als jsonb — heterogene
    -- config, bewust géén losse kolommen.
    instellingen         jsonb       not null default '{}'::jsonb,
    -- ID-tellers (compat met de huidige app die client-side id's toekent)
    volgende_project_id  bigint      not null default 1,
    volgende_klant_id    bigint      not null default 1,
    -- Abonnement (Fase 5) — GERESERVEERD, nog niet gebruikt
    plan                 text        not null default 'free',
    subscription_status  text        not null default 'trial',
    trial_ends_at        timestamptz,
    created_at           timestamptz not null default now(),
    updated_at           timestamptz not null default now()
);

-- ============================================================================
-- USERS  (Fase 2 — GERESERVEERD; 1:1 met Supabase auth.users)
-- Wordt aangemaakt maar nog niet gebruikt door de app.
-- ============================================================================
create table if not exists app_users (
    id          uuid primary key,                       -- = auth.users.id
    company_id  uuid not null references companies(id) on delete cascade,
    email       text,
    role        text not null default 'owner',          -- owner | admin | member
    created_at  timestamptz not null default now()
);
create index if not exists idx_app_users_company on app_users(company_id);

-- ============================================================================
-- KLANTEN
-- ============================================================================
create table if not exists klanten (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint not null,
    naam        text   not null default 'Onbekende klant',
    bedrijf     text   default '',
    adres       text   default '',
    postcode    text   default '',
    stad        text   default '',
    telefoon    text   default '',
    email       text   default '',
    btw_nummer  text   default '',
    kvk         text   default '',
    notities    text   default '',
    actief      boolean not null default true,
    aangemaakt  text   default '',
    created_by  uuid   references app_users(id) on delete set null,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (company_id, id)
);

-- ============================================================================
-- PRODUCTEN
-- ============================================================================
create table if not exists producten (
    company_id       uuid   not null references companies(id) on delete cascade,
    id               bigint not null,
    naam             text   not null default 'Product',
    prijs            numeric not null default 0,
    verbruik         numeric not null default 0,
    eenheid          text   default 'stuk',
    categorie        text   default 'Overig',
    werkzaamheden    jsonb  not null default '[]'::jsonb,
    inhoud           numeric default 0,
    inhoud_eenheid   text   default '',
    verbruik_eenheid text   default 'm²',
    actief           boolean not null default true,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    primary key (company_id, id)
);

-- ============================================================================
-- PERSONEEL
-- ============================================================================
create table if not exists personeel (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint not null,
    naam        text   not null default 'Medewerker',
    uurtarief   numeric not null default 0,
    functie     text   default '',
    telefoon    text   default '',
    email       text   default '',
    notities    text   default '',
    actief      boolean not null default true,
    status      text   default 'Actief',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    primary key (company_id, id)
);

-- ============================================================================
-- PROJECTEN
--  onderdelen = jsonb (project-eigen regelitems, altijd mét het project benaderd)
--  prijs_snapshot = jsonb (BEVROREN calculatie — exact bewaren, nooit herberekenen)
-- ============================================================================
create table if not exists projecten (
    company_id     uuid   not null references companies(id) on delete cascade,
    id             bigint not null,
    naam           text   not null default 'Naamloos project',
    klant_id       bigint,
    adres          text   default '',
    status         text   default 'Concept',
    aangemaakt     text   default '',
    notities       text   default '',
    btw            numeric,
    marge          numeric,
    onderdelen     jsonb  not null default '[]'::jsonb,
    offerte_nummer text,
    factuur_nummer text,
    factuur_datum  text,
    prijs_snapshot jsonb,
    created_by     uuid   references app_users(id) on delete set null,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    primary key (company_id, id),
    -- klant verwijderen → cascade naar diens projecten (zoals de app het ook doet).
    -- (SET NULL kan hier niet: company_id maakt deel uit van de FK en is NOT NULL.)
    foreign key (company_id, klant_id)
        references klanten(company_id, id) on delete cascade
);
create index if not exists idx_projecten_klant on projecten(company_id, klant_id);

-- ============================================================================
-- PROJECT_PERSONEEL  (echte M:N — vervangt de dubbele arrays
--                     project.medewerkers[] + personeel.project_ids[])
-- ============================================================================
create table if not exists project_personeel (
    company_id   uuid   not null references companies(id) on delete cascade,
    project_id   bigint not null,
    personeel_id bigint not null,
    primary key (company_id, project_id, personeel_id),
    foreign key (company_id, project_id)
        references projecten(company_id, id) on delete cascade,
    foreign key (company_id, personeel_id)
        references personeel(company_id, id) on delete cascade
);

-- ============================================================================
-- OFFERTES  (first-class — eigen nummer/datum/snapshot/status)
-- ============================================================================
create table if not exists offertes (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint generated by default as identity,
    project_id  bigint not null,
    nummer      text   not null,
    datum       text,
    geldig_tot  text,
    status      text   default 'concept',
    snapshot    jsonb,
    created_at  timestamptz not null default now(),
    primary key (company_id, id),
    foreign key (company_id, project_id)
        references projecten(company_id, id) on delete cascade
);
create index if not exists idx_offertes_project on offertes(company_id, project_id);

-- ============================================================================
-- FACTUREN  (first-class — concept | verzonden | betaald)
-- ============================================================================
create table if not exists facturen (
    company_id   uuid   not null references companies(id) on delete cascade,
    id           bigint generated by default as identity,
    project_id   bigint not null,
    nummer       text   not null,
    factuurdatum text,
    vervaldatum  text,
    bedrag       numeric,
    status       text   default 'concept',
    snapshot     jsonb,
    created_at   timestamptz not null default now(),
    primary key (company_id, id),
    foreign key (company_id, project_id)
        references projecten(company_id, id) on delete cascade
);
create index if not exists idx_facturen_project on facturen(company_id, project_id);

-- ============================================================================
-- TAKEN
-- ============================================================================
create table if not exists taken (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint not null,
    taak        text   default '',
    klaar       boolean not null default false,
    datum       text   default '',
    created_at  timestamptz not null default now(),
    primary key (company_id, id)
);

-- ============================================================================
-- AGENDA_ITEMS  (vervangt agenda_taken: dict-van-datum → rijen)
-- ============================================================================
create table if not exists agenda_items (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint generated by default as identity,
    datum       text   not null,            -- YYYY-MM-DD
    tijd        text   default '',
    titel       text   default '',
    subtitel    text   default '',
    status      text   default '',
    payload     jsonb  not null default '{}'::jsonb,   -- overige velden veilig bewaren
    created_at  timestamptz not null default now(),
    primary key (company_id, id)
);
create index if not exists idx_agenda_datum on agenda_items(company_id, datum);

-- ============================================================================
-- ACTIVITEITEN  (audit-log — Fase 3/4; GERESERVEERD)
-- ============================================================================
create table if not exists activiteiten (
    company_id  uuid   not null references companies(id) on delete cascade,
    id          bigint generated by default as identity,
    user_id     uuid   references app_users(id) on delete set null,
    entiteit    text,
    actie       text,
    payload     jsonb,
    created_at  timestamptz not null default now(),
    primary key (company_id, id)
);
create index if not exists idx_activiteiten_company on activiteiten(company_id, created_at desc);

-- ============================================================================
-- ROW LEVEL SECURITY  (voorbereid op login — Fase 2/3)
-- ----------------------------------------------------------------------------
-- RLS staat AAN op elke tabel. De policies isoleren op company_id uit de JWT-claim.
-- FASE 1: de app gebruikt de SERVICE_ROLE key → RLS wordt gebypassed binnen één
--         default company (er is nog geen login).
-- FASE 2: app stapt over op de ANON key + Supabase Auth; de JWT krijgt een custom
--         claim 'company_id' (via een auth hook) → onderstaande policies handhaven
--         dan automatisch de tenant-isolatie. Geen schemawijziging meer nodig.
-- ============================================================================
alter table companies          enable row level security;
alter table app_users          enable row level security;
alter table klanten            enable row level security;
alter table producten          enable row level security;
alter table personeel          enable row level security;
alter table projecten          enable row level security;
alter table project_personeel  enable row level security;
alter table offertes           enable row level security;
alter table facturen           enable row level security;
alter table taken              enable row level security;
alter table agenda_items       enable row level security;
alter table activiteiten       enable row level security;

-- Helper: company_id uit de JWT (null als er geen claim is → geen toegang via anon)
create or replace function auth_company_id() returns uuid
language sql stable as $$
    select nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'company_id', '')::uuid
$$;

-- Generieke tenant-policy per business-tabel (select/insert/update/delete).
do $$
declare t text;
begin
    foreach t in array array[
        'klanten','producten','personeel','projecten','project_personeel',
        'offertes','facturen','taken','agenda_items','activiteiten'
    ] loop
        execute format(
            'drop policy if exists tenant_isolatie on %I;', t);
        execute format(
            'create policy tenant_isolatie on %I
               using (company_id = auth_company_id())
               with check (company_id = auth_company_id());', t);
    end loop;
end $$;

-- companies: eigen rij zichtbaar
drop policy if exists tenant_companies on companies;
create policy tenant_companies on companies
    using (id = auth_company_id())
    with check (id = auth_company_id());

-- app_users: rijen binnen de eigen company
drop policy if exists tenant_app_users on app_users;
create policy tenant_app_users on app_users
    using (company_id = auth_company_id())
    with check (company_id = auth_company_id());

-- ============================================================================
-- GRANTS  (rol-rechten op de tabellen)
-- ----------------------------------------------------------------------------
-- Nodig omdat nieuwe Supabase-projecten (met de nieuwe sb_publishable_/sb_secret_
-- API-sleutels) de automatische grants op public-tabellen niet altijd zetten →
-- anders 'permission denied for table' (42501) bij ELKE query, ook met de secret
-- key (= service_role). RLS blijft de rijen filteren voor anon/authenticated;
-- service_role heeft BYPASSRLS. Idempotent, veilig te herhalen. Bewust GEEN grant
-- op functions (de auth-hook wordt in supabase_rls.sql juist afgeschermd).
-- ============================================================================
grant usage on schema public to anon, authenticated, service_role;
grant all on all tables    in schema public to anon, authenticated, service_role;
grant all on all sequences in schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;

-- ============================================================================
-- KLAAR. Default-company + data komen via migrate_json_to_supabase.py.
-- ============================================================================

-- ######## SECTIE 2/4 — RLS ########
-- ============================================================================
-- CoatFlow — Row Level Security (Fase 3: gebruikersisolatie op databaseniveau)
-- ----------------------------------------------------------------------------
-- Draai dit ÉÉNMALIG in de Supabase SQL-editor. Idempotent (veilig te herhalen).
--
-- Wat dit doet:
--   1. Zet RLS AAN op alle business-tabellen.
--   2. Maakt per tabel een tenant-policy: een rij is alleen zichtbaar/wijzigbaar
--      als company_id == de company_id uit het JWT van de ingelogde gebruiker.
--   3. Maakt een Auth Hook die company_id in het JWT zet (uit app_users), zodat
--      de policies dat ook echt kunnen afdwingen.
--
-- BELANGRIJK:
--   * De app draait nu met de SERVICE_ROLE key → die heeft BYPASSRLS en blijft
--     dus gewoon werken (de isolatie van de app komt nu van de company_id-filters
--     in db.py). RLS sluit hier de PUBLIEKE kant (anon/publishable key, directe
--     API) af: zonder geldig JWT-met-company_id krijgt niemand één rij te zien.
--   * Wil je dat RLS óók de app-queries afdwingt (niet alleen het code-filter),
--     dan stapt de app later over op de anon key + het JWT van de gebruiker.
--     De hook hieronder maakt dat mogelijk; de schemawijziging is dan niet meer
--     nodig.
-- ============================================================================

-- ── 1. Helper: company_id uit het JWT (null = geen toegang) ──────────────────
create or replace function public.auth_company_id()
returns uuid
language sql
stable
as $$
    select nullif(
        current_setting('request.jwt.claims', true)::jsonb ->> 'company_id', ''
    )::uuid
$$;

-- ── 2. RLS aanzetten op alle tabellen ────────────────────────────────────────
alter table public.companies          enable row level security;
alter table public.app_users          enable row level security;
alter table public.klanten            enable row level security;
alter table public.producten          enable row level security;
alter table public.personeel          enable row level security;
alter table public.projecten          enable row level security;
alter table public.project_personeel  enable row level security;
alter table public.offertes           enable row level security;
alter table public.facturen           enable row level security;
alter table public.taken              enable row level security;
alter table public.agenda_items       enable row level security;
alter table public.activiteiten       enable row level security;

-- ── 3. Tenant-policies (SELECT/INSERT/UPDATE/DELETE) op company_id ────────────
-- Alle business-tabellen die een company_id-kolom hebben:
do $$
declare t text;
begin
    foreach t in array array[
        'klanten','producten','personeel','projecten','project_personeel',
        'offertes','facturen','taken','agenda_items','activiteiten'
    ] loop
        execute format('drop policy if exists tenant_isolatie on public.%I;', t);
        execute format($f$
            create policy tenant_isolatie on public.%I
                for all
                to authenticated
                using      (company_id = public.auth_company_id())
                with check (company_id = public.auth_company_id());
        $f$, t);
    end loop;
end $$;

-- companies: alleen je eigen bedrijf
drop policy if exists tenant_companies on public.companies;
create policy tenant_companies on public.companies
    for all to authenticated
    using      (id = public.auth_company_id())
    with check (id = public.auth_company_id());

-- app_users: alleen gebruikers binnen je eigen bedrijf
drop policy if exists tenant_app_users on public.app_users;
create policy tenant_app_users on public.app_users
    for all to authenticated
    using      (company_id = public.auth_company_id())
    with check (company_id = public.auth_company_id());

-- ── 4. Auth Hook: zet company_id in het JWT (uit app_users) ───────────────────
-- Hierdoor bevat het access-token van een ingelogde gebruiker zijn company_id,
-- waar de policies hierboven op filteren.
create or replace function public.custom_access_token_hook(event jsonb)
returns jsonb
language plpgsql
stable
as $$
declare
    cid uuid;
    new_claims jsonb;
begin
    select company_id into cid
    from public.app_users
    where id = (event ->> 'user_id')::uuid
    limit 1;

    new_claims := coalesce(event -> 'claims', '{}'::jsonb);
    if cid is not null then
        new_claims := jsonb_set(new_claims, '{company_id}', to_jsonb(cid::text), true);
    end if;
    return jsonb_set(event, '{claims}', new_claims);
end;
$$;

-- Rechten: alleen de auth-service mag de hook draaien + app_users lezen.
grant usage  on schema public                          to supabase_auth_admin;
grant execute on function public.custom_access_token_hook(jsonb) to supabase_auth_admin;
grant select on public.app_users                       to supabase_auth_admin;
revoke execute on function public.custom_access_token_hook(jsonb) from authenticated, anon, public;

-- ============================================================================
-- NA HET DRAAIEN — één handmatige stap in het dashboard:
--   Authentication → Hooks (Beta) → "Customize Access Token (JWT) Claims"
--   → Enable → kies functie:  public.custom_access_token_hook
-- Daarna bevatten nieuwe JWT's company_id. Bestaande sessies: één keer opnieuw
-- inloggen (of token verversen).
--
-- CONTROLE: de app (service_role) blijft gewoon werken. Test daarnaast met de
-- anon key zónder login → je hoort 0 rijen terug te krijgen op elke tabel.
-- ============================================================================

-- ######## SECTIE 3/4 — TRIGGER ########
-- ============================================================================
-- CoatFlow — Auto-provisioning trigger
-- ----------------------------------------------------------------------------
-- Doel: elke NIEUWE gebruiker krijgt ALTIJD direct een bedrijf + koppeling in
--       public.app_users (met company_id), ook al moet hij zijn e-mail nog
--       bevestigen. Dit gebeurt op databaseniveau (trigger), niet in Python —
--       cruciaal voor de RLS-isolatie en betrouwbaar bij e-mailbevestiging/OAuth.
--
-- Draai dit ÉÉNMALIG in de Supabase SQL-editor (idempotent, veilig te herhalen).
-- ============================================================================

-- Functie: maak bij een nieuwe auth.users-rij een bedrijf + app_users-koppeling.
-- SECURITY DEFINER → draait met de rechten van de eigenaar (postgres), zodat hij
-- in public.companies / public.app_users mag schrijven ondanks RLS.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    new_company_id uuid := gen_random_uuid();
begin
    -- 1. Nieuw, leeg bedrijf voor deze gebruiker
    insert into public.companies (id, naam)
    values (new_company_id, 'Mijn bedrijf');

    -- 2. Koppel de gebruiker als eigenaar aan dat bedrijf
    insert into public.app_users (id, company_id, email, role)
    values (new.id, new_company_id, new.email, 'owner');

    return new;
end;
$$;

-- Trigger: vuur ná het aanmaken van een auth-gebruiker.
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user();

-- Mag de auth-service de functie uitvoeren (defensief; trigger werkt doorgaans
-- ook zonder, maar dit voorkomt rechten-verrassingen).
grant execute on function public.handle_new_user() to supabase_auth_admin;

-- ============================================================================
-- CONTROLE: registreer een testaccount → er hoort meteen een rij te staan in
--   public.app_users (met company_id) én een nieuw bedrijf in public.companies,
--   nog vóór de e-mail is bevestigd.
-- Bestaande gebruikers worden NIET geraakt (de trigger vuurt alleen op nieuwe).
-- ============================================================================

-- ######## SECTIE 4/4 — ADMIN ########
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
