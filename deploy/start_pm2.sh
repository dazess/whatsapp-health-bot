#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/opt/whatsapp-health-bot/whatsapp-health-bot"

cd "$PROJECT_DIR"
source .venv/bin/activate

pm2 delete healthbot-web healthbot-scheduler healthbot-wa-service >/dev/null 2>&1 || true
pm2 start ecosystem.config.cjs --update-env
pm2 save

# Ensure PM2 restarts after reboot
pm2 startup systemd -u "$USER" --hp "$HOME" | tail -n 1

echo "PM2 services started. Use: pm2 status and pm2 logs"
