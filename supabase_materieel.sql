-- ============================================================================
-- MIGRATIE — "Materieel & Overig" op projecten
-- ============================================================================
-- Voegt de kolom `materieel` toe aan de bestaande tabel `projecten`.
--
-- WAAROM NODIG: db.py slaat een project op via een expliciete witte lijst van
-- kolommen (_project_row). Zonder deze kolom bestaat het veld niet in Postgres
-- en verdwijnen toegevoegde materieelregels stil bij de eerste save — lokaal in
-- JSON-modus werkt het dan wél, online niet.
--
-- DRAAIEN OP: Supabase → SQL Editor, op BEIDE projecten:
--   1. coatflow-dev   (eerst testen)
--   2. coatflow       (productie)
--
-- VEILIG: idempotent (if not exists) en puur additief — bestaande projecten
-- krijgen een lege lijst, er wordt niets overschreven of verwijderd.
-- ============================================================================

alter table projecten
    add column if not exists materieel jsonb not null default '[]'::jsonb;

-- Controle: hoort 0 rijen terug te geven (alles heeft een geldige waarde).
-- select id, naam, materieel from projecten where materieel is null;
