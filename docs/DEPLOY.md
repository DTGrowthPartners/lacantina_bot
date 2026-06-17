# Deploy — Cantina Bot

Cómo levantar el bot en local y en producción (VPS).

> Este doc asume que el **backend de mesas** (`cantina-mesas-backend`) ya está
> corriendo en `https://cantina-api.dtgrowthpartners.com` y que tienes la
> API key. Si no, ver [`API_BACKEND.md`](API_BACKEND.md) para conectar.

---

## Local (Windows / Linux / Mac)

### 1. Prerequisitos
- Python 3.12+
- Postgres 14+ (local o Docker)
- Git

### 2. Setup

```bash
# Clonar o copiar el proyecto a tu máquina
cd ruta/al/cantina_bot

# Crear venv
python -m venv venv
source venv/bin/activate         # Linux / Mac
# venv\Scripts\activate          # Windows

# Instalar deps
pip install -e .

# Configurar entorno
cp .env.example .env
# Editar .env: rellenar ANTHROPIC_API_KEY, WHAPI_TOKEN, CANTINA_API_KEY,
# DATABASE_URL, ADMIN_USER, ADMIN_PASSWORD, ADMIN_SESSION_SECRET
```

### 3. Crear DB

```bash
# Crear usuario + DB Postgres
sudo -u postgres createuser cantina_user --pwprompt    # CHANGEME
sudo -u postgres createdb cantina_db -O cantina_user

# Aplicar schema (después de Paso 5 del ROADMAP — schema.sql limpio)
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f schema.sql

# Migrations
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/002_equipo_a_db.sql
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/008_etiquetas_contacto.sql
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/009_tags_seguimiento.sql
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/013_difusiones.sql

# Seed del staff (después de Paso 7)
python scripts/seed_staff.py
```

### 4. Arrancar

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8012 --reload
```

Abrir: `http://127.0.0.1:8012/admin` → login con `ADMIN_USER` / `ADMIN_PASSWORD`.

### 5. Smoke test

```bash
curl http://127.0.0.1:8012/health        # debe responder {"status":"ok"}
```

Test del backend de mesas (sin auth):
```bash
curl https://cantina-api.dtgrowthpartners.com/api/health
```

---

## Producción (VPS Ubuntu)

Asume VPS `149.56.133.201` (mismo donde está `bot_asistente` y
`cantina-mesas-backend`).

### 1. Crear usuario / DB

```bash
ssh ubuntu@149.56.133.201

sudo -u postgres createuser cantina_user --pwprompt
sudo -u postgres createdb cantina_db -O cantina_user
```

### 2. Subir código

```bash
mkdir -p /home/ubuntu/cantina-bot && cd /home/ubuntu/cantina-bot
# scp o git clone
```

### 3. Venv + deps

```bash
cd /home/ubuntu/cantina-bot
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

### 4. `.env` en producción

```bash
nano .env
# Rellenar todo. Especialmente:
# BOT_ENV=production
# DATABASE_URL apuntando a Postgres local
# WHAPI_WEBHOOK_URL=https://cantina-bot.dtgrowthpartners.com/webhook
# Puerto 8012 (no chocar con bot_asistente que usa 8011)
chmod 600 .env
```

### 5. Schema + migrations + seed

Igual que local pero apuntando a la DB de producción.

### 6. systemd-user service

Crear `~/.config/systemd/user/cantina-bot.service`:

```ini
[Unit]
Description=Cantina Bot — WhatsApp reservas La Cantina Plus
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/ubuntu/cantina-bot
Environment="PATH=/home/ubuntu/cantina-bot/venv/bin"
ExecStart=/home/ubuntu/cantina-bot/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8012
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Habilitar lingering (para que arranque sin login):

```bash
sudo loginctl enable-linger ubuntu

systemctl --user daemon-reload
systemctl --user enable cantina-bot.service
systemctl --user start cantina-bot.service
systemctl --user status cantina-bot.service
```

### 7. Nginx reverse proxy + SSL

```nginx
# /etc/nginx/sites-available/cantina-bot.dtgrowthpartners.com
server {
    listen 443 ssl http2;
    server_name cantina-bot.dtgrowthpartners.com;

    ssl_certificate /etc/letsencrypt/live/cantina-bot.dtgrowthpartners.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/cantina-bot.dtgrowthpartners.com/privkey.pem;

    client_max_body_size 30M;

    location / {
        proxy_pass http://127.0.0.1:8012;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}

server {
    listen 80;
    server_name cantina-bot.dtgrowthpartners.com;
    return 301 https://$host$request_uri;
}
```

```bash
sudo ln -s /etc/nginx/sites-available/cantina-bot.dtgrowthpartners.com /etc/nginx/sites-enabled/
sudo certbot --nginx -d cantina-bot.dtgrowthpartners.com
sudo nginx -t && sudo systemctl reload nginx
```

### 8. Configurar webhook en whapi

En el panel de whapi.cloud → canal de La Cantina → Settings → Webhook:

```
URL: https://cantina-bot.dtgrowthpartners.com/webhook
Events: messages.post, statuses.post
Mode: body
```

Test:
```bash
curl https://cantina-bot.dtgrowthpartners.com/health
# {"status":"ok",...}
```

### 9. Logs

```bash
# En vivo
journalctl --user -u cantina-bot.service -f

# Filtrar errores
journalctl --user -u cantina-bot.service -p err --since '1h ago'

# Buscar por cliente
journalctl --user -u cantina-bot.service --since '1d ago' | grep '+57300...'
```

---

## Mantenimiento

### Cambios de código

```bash
cd /home/ubuntu/cantina-bot
# subir cambios (git pull, scp, etc.)
systemctl --user restart cantina-bot.service
```

### Cambios de schema

Crear nueva migration `migrations/0XX_descripcion.sql`, aplicar:

```bash
psql -h 127.0.0.1 -U cantina_user -d cantina_db -f migrations/010_xxx.sql
systemctl --user restart cantina-bot.service
```

### Rotar API keys

Si la API key de Cantina se filtra, rotar:
1. En el backend Node (`cantina-mesas-backend`), generar nueva key.
2. Actualizar `.env` del bot: `CANTINA_API_KEY=nuevo_valor`.
3. Reiniciar el bot.

### Backups

- DB del bot: `pg_dump cantina_db > backup_$(date +%F).sql` (cron diario).
- DB de mesas: ese backup lo maneja el proyecto `cantina-mesas-backend`.

---

## Troubleshooting

| Síntoma | Causa probable | Acción |
|---------|----------------|--------|
| Bot no responde nada | whapi no llega al webhook | Ver `journalctl`. Reconfigurar webhook en whapi. |
| Bot dice "no pude consultar la agenda" | API key Cantina inválida | Revisar `CANTINA_API_KEY` y `healthcheck()`. |
| Mismo cliente recibe 3 respuestas | Race condition guard rota | Revisar `flows/conversation.py` — guard al final, debe abortar si llegó nuevo inbound. |
| El bot responde a Fabio como si fuera cliente | Falta seed `equipo_miembros` | Ejecutar `scripts/seed_staff.py`. |
| Las alertas no llegan al grupo | `EQUIPO_CANTINA_GROUP_ID` mal en `.env` | Sacar el ID via `GET /groups` de whapi, copiar formato `1203...@g.us`. |
| Postgres "out of connections" | pool muy chico | Subir `db_pool_size` en `config.py`. |
