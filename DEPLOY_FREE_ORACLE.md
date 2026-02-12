# Free Online Deployment (Oracle Cloud Always Free)

This guide deploys both services securely on one Ubuntu VM:
- Flask app (Gunicorn) on `127.0.0.1:5000`
- WhatsApp Baileys service on `127.0.0.1:3000`
- Nginx public reverse proxy on `80/443`
- PM2 process manager with reboot persistence

## 1) Provision VM
- Create an Ubuntu 22.04/24.04 Always Free instance.
- Open inbound ports: `22`, `80`, `443` (security list + local firewall).

## 2) Upload project
Copy this repo to:
`/opt/whatsapp-health-bot/whatsapp-health-bot`

## 3) Bootstrap server
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
chmod +x deploy/bootstrap_ubuntu.sh deploy/start_pm2.sh deploy/enable_https_certbot.sh
./deploy/bootstrap_ubuntu.sh
```

## 4) Configure secrets
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
cp .env.example .env
nano .env
```
Set strong random values for:
- `FLASK_SECRET_KEY`
- `ENCRYPTION_KEY`
- `WA_SERVICE_API_KEY`
- `WHATSAPP_WEBHOOK_TOKEN`

Also set real Google OAuth values and `ADMIN_EMAILS`.

## 5) Start services
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
./deploy/start_pm2.sh
pm2 status
```

## 6) Link WhatsApp device
```bash
pm2 logs healthbot-wa-service --lines 200
```
Scan QR code once from your WhatsApp app.

## 7) Enable HTTPS
Point your domain A record to VM public IP, then:
```bash
sudo ./deploy/enable_https_certbot.sh your-domain.com you@example.com
```

## Security notes
- Public traffic only goes to Nginx (`80/443`). App ports are localhost-only.
- Flask <-> wa-service calls require shared secret headers.
- Session cookies are HttpOnly + SameSite + Secure in production.
- Keep `.env` private and never commit it.
- Rotate secrets if leaked.
- Back up SQLite (`instance/*.db`) and `wa-service/auth_info_baileys/` regularly.

## Update workflow
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
cd wa-service && npm ci && cd ..
pm2 restart all --update-env
```
