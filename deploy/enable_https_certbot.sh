#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 <domain> <email>"
  exit 1
fi

DOMAIN="$1"
EMAIL="$2"

sudo apt update
sudo apt install -y certbot python3-certbot-nginx

sudo sed -i "s/server_name YOUR_DOMAIN_OR_IP;/server_name ${DOMAIN};/" /etc/nginx/sites-available/healthbot
sudo nginx -t
sudo systemctl reload nginx

sudo certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "$EMAIL" --redirect
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

echo "HTTPS enabled for ${DOMAIN}."
