# Deploy on GCP Free Tier (Compute Engine e2-micro)

This guide runs your full stack on one Ubuntu VM:
- Flask app (Gunicorn) on `127.0.0.1:5000`
- WhatsApp Baileys service on `127.0.0.1:3000`
- Nginx reverse proxy on `80/443`
- PM2 for process management and auto-restart

## 0) Free tier notes
- Use **Compute Engine e2-micro** in free-tier eligible US regions (for example: `us-west1`, `us-central1`, `us-east1`).
- Keep one small standard persistent disk only.
- Avoid paid resources (load balancer, Cloud SQL, premium disks, static IP when VM is stopped).
- Billing account is still required by GCP.

## 1) Create the VM
1. Go to **Compute Engine > VM instances > Create instance**.
2. Recommended settings:
   - Name: `healthbot-vm`
   - Region: free-tier eligible US region
   - Machine: `e2-micro`
   - OS: Ubuntu 22.04 or 24.04 LTS
   - Boot disk: Standard persistent disk (small size)
   - Firewall: allow HTTP + HTTPS
3. Create instance.

## 2) Open firewall ports
Use VPC firewall rules (or VM network tags) to allow inbound:
- `22` (SSH)
- `80` (HTTP)
- `443` (HTTPS)

## 3) SSH and upload project
SSH into VM from GCP Console, then place project at:
`/opt/whatsapp-health-bot/whatsapp-health-bot`

Example (inside VM):
```bash
sudo mkdir -p /opt/whatsapp-health-bot
sudo chown -R "$USER":"$USER" /opt/whatsapp-health-bot
cd /opt/whatsapp-health-bot
# then git clone your repo so it becomes /opt/whatsapp-health-bot/whatsapp-health-bot
```

## 4) Bootstrap server
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
chmod +x deploy/bootstrap_ubuntu.sh deploy/start_pm2.sh deploy/enable_https_certbot.sh
./deploy/bootstrap_ubuntu.sh
```

## 5) Configure secrets
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

Also set real values for:
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `ADMIN_EMAILS`

Generate keys quickly:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

## 6) Start services
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
./deploy/start_pm2.sh
pm2 status
```

## 7) Link WhatsApp account (first time only)
```bash
pm2 logs healthbot-wa-service --lines 200
```
Scan the QR code from your WhatsApp app.

## 8) HTTPS with domain (recommended)
Point your domain A record to VM external IP, then:
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
sudo ./deploy/enable_https_certbot.sh your-domain.com you@example.com
```

## 9) Optional: no domain (temporary)
You can access over HTTP via VM external IP, but do not use this long-term for admin login.

## 10) Operations
Check app logs:
```bash
pm2 logs
```

Restart services after update:
```bash
cd /opt/whatsapp-health-bot/whatsapp-health-bot
git pull
source .venv/bin/activate
pip install -r requirements.txt
cd wa-service && npm ci && cd ..
pm2 restart all --update-env
```

## Security checklist
- Keep app ports local-only (`127.0.0.1`), expose only Nginx `80/443`.
- Keep `.env` private; never commit it.
- Rotate secrets if leaked.
- Keep Ubuntu updated:
```bash
sudo apt update && sudo apt upgrade -y
```
- Back up both:
  - `instance/*.db`
  - `wa-service/auth_info_baileys/`
