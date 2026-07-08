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
-- KLAAR. Default-company + data komen via migrate_json_to_supabase.py.
-- ============================================================================
