# CoatFlow — lokaal ontwikkelen & veilig testen

Doel: bugs die **alleen online** verschijnen (login, cookies, Supabase, secrets)
lokaal kunnen reproduceren, zodat wijzigingen weer soepel gaan zonder elke keer
naar de live app te pushen.

---

## 1. De app lokaal draaien (twee modi)

### A) Snelle UI-modus (JSON, geen login) — standaard
Zonder `.streamlit/secrets.toml` draait de app op lokale JSON-opslag
(`data/appdata.json`), **zonder** loginscherm. Ideaal voor snelle UI-wijzigingen.

```powershell
python -m streamlit run SchilderTool1.py
```

### B) Productie-achtige modus (Supabase + login)
Mét een ingevulde `.streamlit/secrets.toml` draait de app precies zoals online:
login, sessie-cookies, multi-tenant data-isolatie. **Hier vind je de cloud-bugs.**

---

## 2. Een DEV/STAGING Supabase opzetten (eenmalig)

> Gebruik **nooit** je productie-Supabase voor lokaal testen — dan raak je echte
> klantdata. Maak een los project.

1. Ga naar https://supabase.com → **New project** (bv. `coatflow-dev`).
2. Draai in de **SQL Editor** van dat dev-project achtereenvolgens:
   - `supabase_schema.sql`   (tabellen + structuur)
   - `supabase_rls.sql`      (row-level security policies)
   - `supabase_trigger.sql`  (auto-company bij registratie)
   - `supabase_admin.sql`    (admin-veld/functies, indien gebruikt)
3. **Project Settings → API** → kopieer:
   - Project URL            → `supabase_url`
   - `anon` `public` key    → `supabase_anon_key`
   - `service_role` secret  → `supabase_service_key`
4. Kopieer het sjabloon en vul in:
   ```powershell
   Copy-Item .streamlit/secrets.toml.example .streamlit/secrets.toml
   ```
   Zet je dev-waarden in `.streamlit/secrets.toml`.
   Dit bestand staat in `.gitignore` en gaat **nooit** naar GitHub.
5. Start de app (modus B) en registreer een testaccount.

> **Let op cookies:** `localhost` is niet ge-sandboxed zoals Streamlit Cloud, dus
> login-persistentie kan lokaal nét iets anders zijn dan online. Voor het écht
> zeker weten van cookie-gedrag: gebruik de staging-app (stap 4 hieronder).

---

## 3. Veilige deploy-workflow (voorkomt "oude code staat nog live")

De live app op Streamlit Cloud herbouwt automatisch bij elke push naar `main`.
Daarom:

- **Klein & getest?** Commit → `git push` → wacht ~1–2 min → **Ctrl+Shift+R** in de browser.
- Zie je de wijziging niet? Bijna altijd browsercache → hard-refreshen. Anders:
  Streamlit Cloud → **Manage app → logs** en deel de foutmelding.

---

## 4. (Optioneel) Een aparte staging-app op Streamlit Cloud

Zo test je online zónder de productie-app te raken:

1. Maak een branch `dev`:  `git checkout -b dev`  (en push 'm).
2. Maak op share.streamlit.io een **tweede app** gekoppeld aan branch `dev`.
3. Vul in die app's **Secrets** de **dev-Supabase** keys in (niet productie).
4. Workflow: push naar `dev` → test op de staging-URL → pas daarna mergen naar
   `main` (= live).

---

## 5. Handige commando's

```powershell
# App draaien
python -m streamlit run SchilderTool1.py

# Snelle syntax-check zonder de app te starten
python -m py_compile SchilderTool1.py auth.py db.py

# Wat staat er klaar om te pushen?
git status -sb
git log --oneline origin/main..HEAD   # commits die nog niet live zijn
```

---

## 6. Belangrijkste valkuilen (waarom online ≠ lokaal)

| Onderwerp | Lokaal (JSON) | Online (Supabase) |
|---|---|---|
| Login | uit | aan (Supabase Auth) |
| Data | `data/appdata.json` | Supabase Postgres, per-`company_id` geïsoleerd |
| Cookies | localhost, geen sandbox | component-iframe, same-origin (cloud-sandbox blokkeert de oude `window.parent`-hack) |
| `st.context.cookies` | verse headers | idem, maar **stale binnen een rerun** — alleen F5 ververst |
| Secrets | `.streamlit/secrets.toml` | Manage app → Settings → Secrets |

Nooit echte secrets of `data/appdata.json` naar GitHub — beide staan in `.gitignore`.
