# CoatFlow op Render (Frankfurt, gratis) — stap voor stap

Doel: het Streamlit-app'je in **Frankfurt** draaien (dicht bij jou + je Supabase),
zodat elke klik een korte hop is i.p.v. NL → VS. **Je Supabase + data blijven staan;
alleen het app'je verhuist.** Je huidige Streamlit-link blijft bestaan als back-up.

---

## 1. Render-account (gratis, ~2 min)
1. Ga naar **https://render.com** → **Get Started** → **Sign up with GitHub**.
2. Geef Render toegang tot je **CoatFlow**-repo (mag alleen die ene repo zijn).

## 2. App aanmaken via de Blueprint (~2 min)
1. In Render: **New +** → **Blueprint**.
2. Kies de repo **CoatFlow/CoatFlow**.
3. Render vindt automatisch `render.yaml` en toont een service **coatflow**
   (regio Frankfurt, plan Free). Klik **Apply** / **Create**.

## 3. Supabase-secrets toevoegen (BELANGRIJK — ~3 min)
De app leest zijn Supabase-config uit `.streamlit/secrets.toml`. Op Render voeg je die
toe als **Secret File** (staat niet in Git, veilig):

1. Open de service **coatflow** → tab **Environment** → **Secret Files** → **Add Secret File**.
2. **Filename / Path:**  `.streamlit/secrets.toml`
3. **Contents:** plak exact dit, met de sleutels van je **PRODUCTIE**-Supabase
   (dezelfde die nu in Streamlit Cloud → Settings → Secrets staan, zodat je échte data
   verschijnt):
   ```toml
   [supabase]
   supabase_url         = "https://JOUW-PROJECT.supabase.co"
   supabase_anon_key    = "JOUW-ANON-PUBLIC-KEY"
   supabase_service_key = "JOUW-SERVICE-ROLE-KEY"
   ```
4. **Save Changes.**

> Let op: gebruik je **productie**-Supabase-sleutels (niet de dev), anders zie je een
> lege of andere database. Plak ze alleen hier in Render — nooit in een chat.

## 4. Deployen
- Render bouwt automatisch (~3-5 min de eerste keer). Volg **Logs**.
- Klaar? Je krijgt een URL zoals **`https://coatflow.onrender.com`**.

## 5. Testen (de meting)
1. Open de Render-URL → log in met je account.
2. Voeg 1 klant + 1 project toe (of bekijk je bestaande data).
3. **Wissel pagina's en druk knoppen** — voelt het nu direct snel?
   - **Ja** → het lag aan de VS-afstand; probleem opgelost. 🎉
   - **Nee** → dan is het bewezen de rij-opbouw in de code, en pak ik dát gericht aan.

---

## Belangrijk over de gratis tier
- **Slaapstand:** na ~15 min zonder bezoek valt de app in slaap. Het **eerste** bezoek
  daarna duurt ~30-60s (opstarten); dáárna is 'ie weer snel. Dat is de prijs van gratis.
- Wil je later "altijd-aan zonder koude start" (óók gratis): dan verhuizen we naar
  **Oracle Cloud Always Free** (Frankfurt) — zeg het maar, dan lever ik dat plan.

## Als de app niet laadt (websocket-probleem achter de proxy)
Zeldzaam, maar als je een leeg/grijs scherm krijgt: pas in Render de **Start Command**
aan naar:
```
streamlit run SchilderTool1.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true --server.enableCORS=false --server.enableXsrfProtection=false
```
en deploy opnieuw.

## Later: eigen domein
Render laat je gratis een eigen domein (bijv. `app.coatflow.nl`) koppelen onder
**Settings → Custom Domains**.
