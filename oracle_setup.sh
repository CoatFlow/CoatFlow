#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# CoatFlow — Oracle Cloud (Ubuntu) setup-script.
# Draai dit OP de server, ná SSH. Installeert Python + dependencies, zet de
# firewall-poort open en maakt een systemd-service (blijft draaien + herstart
# automatisch bij reboot/crash). Zie ORACLE_DEPLOY.md voor de volledige uitleg.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$HOME/CoatFlow"
REPO="https://github.com/CoatFlow/CoatFlow.git"
PORT=8501

echo "==[1/5] Systeem bijwerken + Python/git installeren =="
sudo apt-get update -y
sudo apt-get install -y python3 python3-venv python3-pip git

echo "==[2/5] Repo ophalen naar $APP_DIR =="
if [ -d "$APP_DIR/.git" ]; then
  git -C "$APP_DIR" pull
else
  git clone "$REPO" "$APP_DIR"
fi

echo "==[3/5] Virtuele omgeving + dependencies (kan op ARM even duren) =="
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

echo "==[4/5] Secrets-bestand voorbereiden =="
mkdir -p "$APP_DIR/.streamlit"
if [ ! -f "$APP_DIR/.streamlit/secrets.toml" ]; then
  cp "$APP_DIR/.streamlit/secrets.toml.example" "$APP_DIR/.streamlit/secrets.toml" 2>/dev/null || true
  echo ">> LET OP: vul je coatflow-Supabase-sleutels in:  nano $APP_DIR/.streamlit/secrets.toml"
fi

echo "==[5/5] Firewall-poort $PORT + systemd-service =="
# VM-firewall (Oracle-Ubuntu blokkeert standaard alles behalve SSH):
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || true
sudo apt-get install -y iptables-persistent 2>/dev/null || true
sudo netfilter-persistent save 2>/dev/null || true

sudo tee /etc/systemd/system/coatflow.service >/dev/null <<UNIT
[Unit]
Description=CoatFlow Streamlit
After=network.target

[Service]
User=$USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/streamlit run SchilderTool1.py --server.port=$PORT --server.address=0.0.0.0 --server.headless=true
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable coatflow

echo ""
echo "==================================================================="
echo " KLAAR met installeren."
echo " 1) Vul je secrets in:   nano $APP_DIR/.streamlit/secrets.toml"
echo " 2) Start de app:        sudo systemctl start coatflow"
echo " 3) Bekijk de status:    sudo systemctl status coatflow"
echo " 4) Open in de browser:  http://<PUBLIEK-IP>:$PORT"
echo "==================================================================="
