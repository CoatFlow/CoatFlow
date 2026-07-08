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
