#!/usr/bin/env bash
set -Eeuo pipefail

# Production redeploy script for WhatsApp Health Bot.
# Usage:
#   ./redeploy.sh
#   BRANCH=main ./redeploy.sh

PROJECT_DIR="/opt/whatsapp-health-bot/whatsapp-health-bot"
BRANCH="${BRANCH:-main}"
APP_NAME_WEB="healthbot-web"
APP_NAME_SCHEDULER="healthbot-scheduler"
APP_NAME_WA="healthbot-wa-service"

log() {
  printf "\n[%s] %s\n" "$(date '+%Y-%m-%d %H:%M:%S')" "$1"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd git
require_cmd pm2
require_cmd python3
require_cmd npm

cd "$PROJECT_DIR"

if [[ ! -f ".env" ]]; then
  echo "Missing .env in $PROJECT_DIR" >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Python virtual environment is missing: $PROJECT_DIR/.venv" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit/stash local changes before redeploy." >&2
  exit 1
fi

log "Fetching latest code from origin/$BRANCH"
git fetch origin "$BRANCH"

log "Applying latest commit with fast-forward only"
git pull --ff-only origin "$BRANCH"

log "Installing Python dependencies"
source .venv/bin/activate
pip install --disable-pip-version-check -r requirements.txt

log "Installing Node.js dependencies"
cd wa-service
npm ci --omit=dev
cd "$PROJECT_DIR"

log "Compiling Python files for quick syntax validation"
python3 -m compileall app.py models.py scheduler_tasks.py services.py time_utils.py wsgi.py scheduler_runner.py

log "Restarting PM2 services with updated environment"
pm2 startOrRestart ecosystem.config.cjs --update-env
pm2 save

log "Current PM2 status"
pm2 status

log "Redeploy completed successfully"
#!/bin/bash

# Deployment script for WhatsApp Health Bot
# Standardizes environment and restarts all PM2 services

PROJECT_DIR="/opt/whatsapp-health-bot/whatsapp-health-bot"

echo "------------------------------------------"
echo "🚀 Starting Full Project Redeploy..."
echo "------------------------------------------"

# 1. Navigate to project directory
cd "$PROJECT_DIR" || { echo "❌ Error: Could not cd to $PROJECT_DIR"; exit 1; }

# 2. Virtual Environment Check
if [ ! -d ".venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv .venv
fi

# 3. Load Environment Variables (for TZ and other settings)
if [ -f ".env" ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    echo "✅ Loaded environment from .env"
fi

# 4. Install/Update Dependencies
echo "📥 Installing/Updating dependencies..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 5. Clear Python Cache
echo "🧹 Clearing __pycache__..."
find . -type d -name "__pycache__" -exec rm -rf {} +

# 6. PM2 Restart
# This uses the ecosystem config which contains web, scheduler, and wa-service
if command -v pm2 &> /dev/null; then
    echo "🔄 Restarting PM2 services via ecosystem.config.cjs..."
    pm2 restart ecosystem.config.cjs --update-env
    pm2 status
else
    echo "⚠️ Warning: PM2 not found. Services not restarted automatically."
fi

echo "------------------------------------------"
echo "✅ Redeploy Complete!"
echo "------------------------------------------"
