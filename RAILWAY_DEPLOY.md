# CoatFlow op Railway (productie) — stappenplan

Railway draait CoatFlow als altijd-aan productie-app, dicht bij NL (EU West/Amsterdam).
Je Supabase (`coatflow`) blijft staan; alleen het app'je draait hier.

> Kosten: Railway heeft **geen gratis tier** meer — reken op **~$5/maand** (usage-based,
> Hobby-plan). Voor een echte SaaS is dat prima; je krijgt een veel sterkere CPU dan
> Render-gratis + geen slaapstand.

De repo is al klaar: het **`Procfile`** vertelt Railway hoe Streamlit te starten, en de
app leest de secrets uit **environment-variabelen** (dat hebben we ingebouwd).

---

## 1. Account + project
1. Ga naar **https://railway.app** → **Login with GitHub**.
2. **New Project** → **Deploy from GitHub repo** → kies **CoatFlow/CoatFlow**.
3. Railway detecteert Python (`requirements.txt`) + het `Procfile` en begint te bouwen.

## 2. Regio op EU zetten (belangrijk voor snelheid)
- Open de service → **Settings** → **Region** → kies **EU West (Amsterdam)**.
- (Staat 'ie standaard op US? Dan is dat je latency-boosdoener — zet 'm op Amsterdam en redeploy.)

## 3. Secrets als environment-variabelen
Service → **Variables** → **New Variable** → voeg deze drie toe (met de sleutels van je
**coatflow**-productieproject: Supabase → coatflow → Settings → API):
```
SUPABASE_URL          = https://JOUW-COATFLOW-PROJECT.supabase.co
SUPABASE_ANON_KEY     = <anon / publishable key>
SUPABASE_SERVICE_KEY  = <service_role / secret key>
```
Railway redeployt automatisch na het opslaan.

## 4. Publieke URL aanzetten
- Service → **Settings** → **Networking** → **Generate Domain**. Je krijgt een
  `...up.railway.app`-URL.

## 5. Testen
Open de URL → log in → klik rond (pagina's, knoppen, agenda). Voel of het **snap-snel**
is, en lees onderaan de tijdelijke **⏱-teller** (server-rerun + save).
- **Snel** → bevestigd dat het de zwakke Render-CPU was → Railway is je productie-host. 🎉
- **Nog steeds traag** → dan is het de code-machinerie (JS → CSS) en pak ik die aan.

---

## Belangrijk
- **Gebruik je `coatflow`-sleutels** (productie), niet dev — anders zie je een andere database.
- **Lokaal blijft op `coatflow-dev`** (je `.streamlit/secrets.toml`) → veilig testen zonder productie te raken.
- **Updaten na een push:** Railway herbouwt automatisch bij elke push naar `main`.

## Later: eigen domein
Service → Settings → Networking → **Custom Domain** → `app.coatflow.nl` koppelen (Railway
regelt HTTPS automatisch).
