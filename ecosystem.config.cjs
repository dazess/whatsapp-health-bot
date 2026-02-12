module.exports = {
  apps: [
    {
      name: 'healthbot-web',
      cwd: '/opt/whatsapp-health-bot/whatsapp-health-bot',
      script: 'gunicorn',
      args: '--config gunicorn.conf.py wsgi:app',
      interpreter: 'none',
      env: {
        FLASK_ENV: 'production',
        SESSION_COOKIE_SECURE: '1',
        BEHIND_PROXY: '1',
      },
      max_restarts: 10,
      restart_delay: 3000,
    },
    {
      name: 'healthbot-scheduler',
      cwd: '/opt/whatsapp-health-bot/whatsapp-health-bot',
      script: 'python3',
      args: 'scheduler_runner.py',
      env: {
        FLASK_ENV: 'production',
        SESSION_COOKIE_SECURE: '1',
      },
      max_restarts: 10,
      restart_delay: 3000,
    },
    {
      name: 'healthbot-wa-service',
      cwd: '/opt/whatsapp-health-bot/whatsapp-health-bot/wa-service',
      script: 'node',
      args: 'index.js',
      env: {
        NODE_ENV: 'production',
        HOST: '127.0.0.1',
        PORT: '3000',
        BOT_WEBHOOK_URL: 'http://127.0.0.1:5000/webhook/whatsapp',
      },
      max_restarts: 10,
      restart_delay: 3000,
    },
  ],
};
