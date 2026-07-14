# CoatFlow op Oracle Cloud Always Free (Frankfurt) — stappenplan

Doel: CoatFlow draaien op een **gratis, altijd-aan server met 4 CPU-cores** in
Frankfurt. Dit is een **volwaardige productie-host** (meerdere gebruikers, geen
slaapstand). Je Supabase (coatflow) blijft staan; alleen het app'je draait hier.

> Tijd: ~30-45 min. Je hebt een creditcard nodig voor de **verificatie** bij
> aanmelden — Oracle schrijft niks af op de "Always Free"-onderdelen.

---

## DEEL A — Account + server aanmaken (in de Oracle-website)

### 1. Account
- Ga naar **https://www.oracle.com/cloud/free/** → **Start for free**.
- Kies bij **Home Region** een EU-regio, bij voorkeur **Germany Central (Frankfurt)**.
  (De regio kun je later niet wijzigen — kies Frankfurt.)
- Rond de aanmelding + verificatie af.

### 2. Maak de server (VM) aan
- Menu (☰) → **Compute → Instances** → **Create instance**.
- **Name:** `coatflow`.
- **Image and shape** → **Edit**:
  - **Image:** `Canonical Ubuntu 22.04`.
  - **Shape** → **Ampere** → **VM.Standard.A1.Flex** → zet **OCPU = 4** en **Memory = 24 GB** (dit valt volledig onder Always Free).
- **Networking:** laat op standaard (maakt een VCN + subnet aan). Zorg dat **"Assign a public IPv4 address" = Yes**.
- **SSH keys:** kies **Generate a key pair for me** → **download** de **private key** (bewaar 'm goed! bv. `coatflow.key`).
- Klik **Create**. Wacht tot de instance **Running** is en noteer het **Public IP address**.

### 3. Poort 8501 openzetten (cloud-firewall)
- Op de instance-pagina → klik onder **Primary VNIC** op de **Subnet**-link → **Security Lists** → open de default security list → **Add Ingress Rules**:
  - **Source CIDR:** `0.0.0.0/0`
  - **IP Protocol:** `TCP`
  - **Destination Port Range:** `8501`
  - **Add Ingress Rules**.

---

## DEEL B — De app installeren (via SSH op de server)

### 4. Verbind met de server
Op je pc (PowerShell), in de map waar je private key staat:
```powershell
# Rechten van de key beperken (eenmalig, anders weigert SSH 'm):
icacls coatflow.key /inheritance:r /grant:r "$($env:USERNAME):(R)"
# Verbinden (vervang <PUBLIEK-IP>):
ssh -i coatflow.key ubuntu@<PUBLIEK-IP>
```
Typ **yes** bij de eerste keer. Je zit nu op de server.

### 5. Installeer alles met één commando
Op de server:
```bash
sudo apt-get update -y && sudo apt-get install -y git
git clone https://github.com/CoatFlow/CoatFlow.git
cd CoatFlow
chmod +x oracle_setup.sh
./oracle_setup.sh
```
Dit installeert Python + dependencies, opent de VM-firewall en maakt de service. (Op ARM kan `pip install` een paar minuten duren — even geduld.)

### 6. Vul je Supabase-secrets in
```bash
nano ~/CoatFlow/.streamlit/secrets.toml
```
Zet erin (met de sleutels van je **coatflow**-productieproject — Supabase → coatflow → Settings → API):
```toml
[supabase]
supabase_url         = "https://JOUW-COATFLOW-PROJECT.supabase.co"
supabase_anon_key    = "<anon / publishable key>"
supabase_service_key = "<service_role / secret key>"
```
Opslaan in nano: **Ctrl+O → Enter**, afsluiten: **Ctrl+X**.

### 7. Start de app
```bash
sudo systemctl start coatflow
sudo systemctl status coatflow      # moet "active (running)" tonen
```

### 8. Open + test
Ga in je browser naar:
```
http://<PUBLIEK-IP>:8501
```
Log in, klik rond (pagina's, knoppen, agenda) en voel of het **snap-snel** is.
- **Snel** → bevestigd dat de gratis Render-CPU de boosdoener was; dit is nu je productie-host. 🎉
- **Nog steeds traag** → dan pak ik de code-machinerie (JS → CSS) aan.

---

## Handige beheer-commando's (op de server)
```bash
sudo systemctl restart coatflow     # herstarten
sudo systemctl stop coatflow        # stoppen
journalctl -u coatflow -f           # live logs (Ctrl+C om te stoppen)
cd ~/CoatFlow && git pull && sudo systemctl restart coatflow   # update na een push
```

## Later: eigen domein + HTTPS (aan te raden voor productie)
Nu draait 'ie op `http://<ip>:8501`. Voor een net adres (`https://app.coatflow.nl`)
zetten we later **nginx + gratis Let's Encrypt-certificaat** ervoor. Zeg het maar
wanneer je zover bent, dan lever ik dat stappenplan.

## Problemen?
- **Pagina laadt niet** → check dat je zowel de **Security List** (stap 3) als de
  **VM-firewall** (script stap 5) hebt gedaan, en dat de service `active (running)` is.
- **Service start niet** → `journalctl -u coatflow -n 50` en stuur me de foutmelding.
- **"Out of host capacity"** bij het aanmaken van de VM → Ampere is even vol in de
  regio; probeer het later opnieuw of een andere availability domain.
