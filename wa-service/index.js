const {
    default: makeWASocket,
    useMultiFileAuthState,
    DisconnectReason,
    fetchLatestBaileysVersion,
    makeCacheableSignalKeyStore,
    isJidUser
} = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const pino = require('pino');
const express = require('express');
const bodyParser = require('body-parser');
const qrcode = require('qrcode-terminal');
const fs = require('fs');
const axios = require('axios');

const app = express();
app.use(bodyParser.json());

const PORT = Number(process.env.PORT || 3000);
const HOST = process.env.HOST || '127.0.0.1';
// URL of the Flask Application
const BOT_WEBHOOK_URL = process.env.BOT_WEBHOOK_URL || 'http://127.0.0.1:5000/webhook/whatsapp';
const WA_SERVICE_API_KEY = (process.env.WA_SERVICE_API_KEY || '').trim();
const WEBHOOK_TOKEN = (process.env.WHATSAPP_WEBHOOK_TOKEN || '').trim();

let sock;
let isWhatsAppReady = false;

async function connectToWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState('auth_info_baileys');
    const { version, isLatest } = await fetchLatestBaileysVersion();
    
    console.log(`using WA v${version.join('.')}, isLatest: ${isLatest}`);

    sock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, pino({ level: "silent" })),
        },
        generateHighQualityLinkPreview: true,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
        const { connection, lastDisconnect, qr } = update;
        
        if (qr) {
            console.log('Scan the QR code below to login:');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'close') {
            isWhatsAppReady = false;
            const shouldReconnect = (lastDisconnect.error instanceof Boom) ?
                lastDisconnect.error.output.statusCode !== DisconnectReason.loggedOut : true;
            
            console.log('connection closed due to ', lastDisconnect.error, ', reconnecting ', shouldReconnect);
            
            if (shouldReconnect) {
                connectToWhatsApp();
            } else {
                console.log('Connection closed. You are logged out.');
                process.exit(0); // Exit so you can restart and scan again if needed
            }
        } else if (connection === 'open') {
            isWhatsAppReady = true;
            console.log('opened connection');
        }
    });

    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type === 'notify') {
            for (const msg of messages) {
                if (!msg.key.fromMe && isJidUser(msg.key.remoteJid)) {
                    // console.log('replying to', msg.key.remoteJid);
                    
                    const sender = msg.key.remoteJid.split('@')[0];
                    let text = msg.message?.conversation || msg.message?.extendedTextMessage?.text || "";
                    
                    if (text) {
                        console.log(`Received message from ${sender}: ${text}`);
                        
                        // Forward to Flask Bot
                        try {
                            const headers = {};
                            if (WEBHOOK_TOKEN) {
                                headers['X-Webhook-Token'] = WEBHOOK_TOKEN;
                            }

                            await axios.post(BOT_WEBHOOK_URL, {
                                sender: sender,
                                message: text
                            }, { headers, timeout: 10000 });
                        } catch (err) {
                            console.error('Failed to forward message to bot:', err.message);
                        }
                    }
                }
            }
        }
    });
}

// API Endpoint to send messages
app.post('/send-message', async (req, res) => {
    const providedApiKey = (req.header('X-Api-Key') || '').trim();
    if (WA_SERVICE_API_KEY && providedApiKey !== WA_SERVICE_API_KEY) {
        return res.status(401).json({ success: false, error: 'Unauthorized' });
    }

    const { phone, message } = req.body;

    if (!sock || !isWhatsAppReady) {
        return res.status(503).json({ error: 'WhatsApp not connected' });
    }

    try {
        const jid = `${phone}@s.whatsapp.net`;
        const exists = await Promise.race([
            sock.onWhatsApp(jid),
            new Promise((_, reject) =>
                setTimeout(() => reject(new Error('onWhatsApp lookup timeout')), 8000)
            ),
        ]);
        
        if (exists && exists[0]?.exists) {
            await sock.sendMessage(jid, { text: message });
            return res.json({ status: 'sent' });
        } else {
            return res.status(404).json({ error: 'Number not registered on WhatsApp' });
        }
    } catch (e) {
        console.error(e);
        return res.status(500).json({ error: e.message });
    }
});

app.listen(PORT, HOST, () => {
    console.log(`Baileys Service running on ${HOST}:${PORT}`);
    connectToWhatsApp();
});
