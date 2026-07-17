# Eigen offerte-/factuursjablonen (Word) — setup & werking

Schilders slepen in **Instellingen → Offertes** (en **→ Facturen**) een fictieve, volledig
ingevulde Word-offerte/-factuur in de app. Eén AI-aanroep herkent welke teksten
projectvelden zijn (datum, nummer, klant, onderdelen-tabel, totalen); na een korte
bevestigingsstap wordt het document automatisch omgezet in een herbruikbaar sjabloon.
Daarna vult **PDF genereren** bij elk project het eigen ontwerp met de echte projectdata.
Geen sjabloon geüpload → de ingebouwde PDF (volledige terugval, nul regressie).

---

## Eenmalige setup

### 1. Database-migratie (Supabase)
Draai **`supabase_sjablonen.sql`** in de Supabase **SQL Editor**, op **beide** projecten:
1. `coatflow-dev`   2. `coatflow` (productie)

Verwacht resultaat: "Success. No rows returned". Idempotent — herhalen is veilig.

### 2. Anthropic API-sleutel (voor de AI-herkenning bij het uploaden)
De herkenning draait één keer per geüpload sjabloon via de Anthropic API.
- **Railway** (test + productie): service → **Variables** → `ANTHROPIC_API_KEY = <sleutel>`
  (sleutel via https://console.anthropic.com → API Keys; nooit in code/chat plakken).
- Lokaal (optioneel): dezelfde variabele als omgevingsvariabele, of in
  `.streamlit/secrets.toml` (root-niveau of onder een `[anthropic]`-blok als `api_key`).

Optioneel: `COATFLOW_AI_MODEL` om het model te kiezen (standaard `claude-sonnet-5`,
een stabiel model; zet bv. `claude-opus-4-8` voor maximale nauwkeurigheid).

Zonder sleutel blijft alles werken — alleen het uploaden van een nieuw sjabloon toont dan
een nette melding dat de sleutel ontbreekt.

### 3. Dependencies
Staan in `requirements.txt` (`python-docx`, `docxtpl`, `anthropic`) — Railway installeert
ze automatisch bij de volgende deploy.

---

## Word → PDF (fases)

- **FASE 1 (nu actief):** met een eigen sjabloon levert "PDF genereren" een ingevulde
  **.docx** (opent in Word; dáár is 'Opslaan als PDF' één klik). Zonder sjabloon blijft
  het de ingebouwde PDF.
- **FASE 2 (optioneel, later):** staat **LibreOffice** op de server, dan detecteert de app
  dat automatisch en levert 'ie direct een **PDF** van het sjabloon. Op Railway kan dat
  door een `nixpacks.toml` toe te voegen met LibreOffice als extra pakket — let op: dit
  maakt het build-image honderden MB's groter en de deploy trager. Pas activeren als
  FASE 1 bevalt; eerst op de testservice proberen.

```toml
# nixpacks.toml — FASE 2 (nog NIET toegevoegd; eerst op staging testen)
[phases.setup]
nixPkgs = ["...", "libreoffice"]
```

---

## Hoe het technisch werkt (kort)
1. **Upload** (.docx) → tekst + tabellen uitgelezen (`python-docx`) → één AI-aanroep
   (herken-JSON: velden + onderdelen-tabel + twijfels) → **bevestigingsstap** in de UI.
2. **Templatiseren**: de bevestigde letterlijke teksten worden vervangen door
   docxtpl-placeholders; de onderdelen-rij wordt een herhaalbare `{%tr for %}`-rij;
   extra voorbeeldrijen verdwijnen. Opslag per bedrijf in de tabel `sjablonen`
   (JSON-modus: `data/sjablonen/`).
3. **Genereren** (deterministisch, géén AI): sjabloon + projectdata
   (`bereken_onderdeel`/`bereken_project_totaal` — exact dezelfde bedragen als de
   ingebouwde PDF) → ingevulde .docx (of PDF in FASE 2). Globaal gecachet op
   projectinhoud + sjabloonversie.
