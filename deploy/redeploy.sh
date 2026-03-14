#!/usr/bin/env bash
# redeploy.sh — pull latest code and reload PM2 services in-place.
# Run this directly on the VPS:
#   bash /opt/whatsapp-health-bot/whatsapp-health-bot/deploy/redeploy.sh
set -euo pipefail

PROJECT_DIR="/opt/whatsapp-health-bot/whatsapp-health-bot"

cd "$PROJECT_DIR"

echo "==> Pulling latest code from origin/main ..."
git fetch origin main
git reset --hard origin/main

echo "==> Updating Python dependencies ..."
source .venv/bin/activate
pip install -q -r requirements.txt

echo "==> Updating Node dependencies ..."
cd wa-service
npm ci --omit=dev
cd ..

echo "==> Reloading PM2 processes ..."
pm2 reload healthbot-web --update-env
pm2 restart healthbot-scheduler --update-env
pm2 restart healthbot-wa-service --update-env
pm2 save

echo ""
echo "Deployment complete. Current PM2 status:"
pm2 status
