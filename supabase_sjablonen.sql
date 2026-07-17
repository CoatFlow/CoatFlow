-- ============================================================================
-- CoatFlow — Eigen offerte-/factuursjablonen (Word) per bedrijf.
-- Draai dit bestand ÉÉN keer in de Supabase SQL Editor, op BEIDE projecten:
--   1) coatflow-dev   2) coatflow (productie)
-- Idempotent: veilig om te herhalen.
-- ============================================================================

-- Sjablonen: per bedrijf max. één offerte- en één factuursjabloon.
--   docx_b64 = het ge-templatiseerde Word-bestand (base64; placeholders al gezet)
--   meta     = herken-mapping + originele bestandsnaam + uploaddatum (jsonb)
create table if not exists sjablonen (
    company_id uuid not null references companies(id) on delete cascade,
    soort      text not null check (soort in ('offerte', 'factuur')),
    docx_b64   text not null,
    meta       jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now(),
    primary key (company_id, soort)
);

-- Tenant-isolatie: zelfde patroon als de overige tabellen (auth_company_id()
-- bestaat al via supabase_schema.sql).
alter table sjablonen enable row level security;

drop policy if exists tenant_isolatie on sjablonen;
create policy tenant_isolatie on sjablonen
    using (company_id = auth_company_id())
    with check (company_id = auth_company_id());

-- Grants (nieuwe projecten zetten deze niet altijd automatisch; service_role
-- heeft BYPASSRLS, anon/authenticated blijven door RLS begrensd).
grant usage on schema public to anon, authenticated, service_role;
grant all on sjablonen to anon, authenticated, service_role;

-- Klaar. Controle: select count(*) from sjablonen;  → "0 rows" is goed.
