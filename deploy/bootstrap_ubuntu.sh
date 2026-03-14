#!/usr/bin/env bash
set -euo pipefail

if [ "${EUID}" -eq 0 ]; then
  echo "Run as a sudo-capable user, not root." >&2
  exit 1
fi

PROJECT_DIR="/opt/whatsapp-health-bot/whatsapp-health-bot"

sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx fail2ban ufw nodejs npm

# Firewall (only SSH + web)
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

# Create app directory if missing
sudo mkdir -p /opt/whatsapp-health-bot
sudo chown -R "$USER":"$USER" /opt/whatsapp-health-bot

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Project not found at $PROJECT_DIR"
  echo "Place your repo there first, then rerun."
  exit 1
fi

cd "$PROJECT_DIR"

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cd "$PROJECT_DIR/wa-service"
npm ci
npm install -g pm2

cd "$PROJECT_DIR"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Edit it before starting services."
fi

sudo cp deploy/nginx-healthbot.conf /etc/nginx/sites-available/healthbot
sudo ln -sf /etc/nginx/sites-available/healthbot /etc/nginx/sites-enabled/healthbot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
sudo systemctl enable nginx

echo "Bootstrap complete. Next: edit .env and deploy/nginx-healthbot.conf (server_name)."
