# CoatFlow (SchilderTool) — projectgids voor Claude Code

SaaS voor schilders: offertes, projecten, calculaties, facturen, klanten/personeel.
Streamlit + Supabase, multi-tenant. Nederlandstalige UI, premium SaaS-uitstraling.

## Belangrijkste bestanden
- `SchilderTool1.py` — de hele app (~9200 regels, monoliet). Alle UI + logica.
  CSS zit in de `_APP_CSS`-string; merk-kleur `#2563EB`.
- `auth.py` — Supabase Auth (login/registratie/uitloggen), sessie-cookie,
  tenant-creatie, route-guard `require_auth()`.
- `db.py` — datatoegang (service_role client) + secrets + JSON-terugval.
- `product_import.py` — URL-parser voor productimport.
- `migrate_json_to_supabase.py` — eenmalige JSON→Postgres migratie.
- `supabase_*.sql` — schema, RLS, trigger, admin (draaien in Supabase SQL Editor).

## Twee runtime-modi (cruciaal!)
- **JSON-modus** (geen `.streamlit/secrets.toml`): lokale opslag `data/appdata.json`,
  **geen login**. Standaard lokaal. Auth is inert.
- **Supabase-modus** (secrets ingevuld): login aan, data per-`company_id` geïsoleerd.
  Dit is productie. `_use_db()` = `_DB_OK and _db.is_enabled()`.

Poort van waarheid voor opslag: `load_data()` / `save_data()` in `SchilderTool1.py`.
Alle CRUD loopt hierlangs → automatisch per-tenant gescoped op `st.session_state.company_id`.

## Lokaal draaien
```powershell
python -m streamlit run SchilderTool1.py          # JSON-modus (snelle UI-dev)
python -m py_compile SchilderTool1.py auth.py db.py  # snelle syntax-check
```
Voor Supabase-modus lokaal + volledige uitleg: zie `DEV_SETUP.md`.

## Deploy
Live op Streamlit Community Cloud, auto-rebuild bij **push naar `main`**.
- Wijziging niet zichtbaar na push? Meestal browsercache → **Ctrl+Shift+R**.
- Cloud-fout? Streamlit Cloud → **Manage app → logs** (vraag de gebruiker die te delen).
- Secrets staan op Cloud in **Settings → Secrets**, niet in de repo.

## Waarom "online ≠ lokaal" (veelvoorkomende bron van bugs)
- **Cookies:** de cloud iframe-sandbox blokkeert `window.parent.document.cookie`.
  Daarom schrijft `auth.py` de sessie-cookie via `streamlit-cookies-controller`
  (schrijft in zijn EIGEN same-origin iframe). Ruwe JS blijft als terugval.
- **`st.context.cookies`** leest uit de INITIËLE request-headers → **stale binnen een
  rerun**, ververst alleen bij een echte page-load (F5). Hield verband met de
  uitlog-bug (sticky `_logged_out`-lock is de fix).
- **Login-persistentie:** cookie `cf_sess` (7 dagen) met de Supabase refresh_token;
  herstel via `cl.auth.refresh_session(token)`. Refresh-terwijl-ingelogd = blijft in;
  uitloggen wist de cookie + zet de lock.

## Werkwijze / conventies
- **Wijzig geen functionaliteit buiten de gevraagde taak.** Check na elke wijziging
  op regressies; behoud de premium uitstraling.
- Hergebruik bestaande helpers/componenten (validatie, kosten-breakdown, houttype).
- Actieve `st.tabs`-tab: transparant + blauwe tekst (geen wit vlak). Sidebar-nav
  actief: `#2563EB`.
- Verifiëren kan via de preview-tools in een geïsoleerde JSON-omgeving
  (`.preview_tmp/empty_secrets.toml`, launchconfig `SchilderToolPreview`).
  `launch.json` bewust op 2 configs houden.

## Veiligheid (hard)
- **Nooit** echte secrets in chat/commits. `.streamlit/secrets.toml`, `secrets.toml`,
  `*.env` staan in `.gitignore`.
- **`data/appdata.json` = echte klantdata** (privacy/AVG), gitignored. Niet committen;
  na lokale tests controleren dat 'ie onveranderd is.
- **Nooit** de productie-Supabase direct muteren om te testen — gebruik een
  dev/staging-project (zie `DEV_SETUP.md`).
- Pushen naar GitHub = deploy naar de live app → doe dit bewust/na overleg.
