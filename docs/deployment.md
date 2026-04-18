# Deployment Guide

## Docker (SQLite)

Suitable for single-server setups, low to medium traffic.

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra/docker

# Create data directory and config
mkdir -p ../data
cp ../config/config.example.yaml ../data/config.yaml
$EDITOR ../data/config.yaml

# Start
docker compose up -d

# Check logs
docker compose logs -f pyra
```

The SQLite database is stored in `data/pyra.db`.

---

## Docker (PostgreSQL)

Recommended for production deployments.

```bash
cd pyra/docker

mkdir -p ../data ../plugins_extra

# Ensure runtime config exists
cp -n ../config/config.example.yaml ../data/config.yaml
$EDITOR ../data/config.yaml

# Set password
cp .env.example .env
$EDITOR .env

# Start with PostgreSQL
docker compose -f docker-compose.prod.yml up -d
```

The `DATABASE_URL` is set automatically via docker-compose environment variables. Alembic migrations run automatically on container start via `entrypoint.sh`.

---

## Deploying to host.domain

```bash
# On remote server
ssh user@host.domain

# Clone repo (first time)
cd /opt
git clone https://github.com/Jarsky/pyra.git pyra
cd pyra/docker

# Copy and configure
mkdir -p ../data ../plugins_extra
cp -n ../config/config.example.yaml ../data/config.yaml
nano ../data/config.yaml

# Set DB password (prod)
cp .env.example .env
nano .env

# Start
docker compose -f docker-compose.prod.yml up -d
```

### Updating

```bash
cd /opt/pyra
git pull
cd docker
docker compose -f docker-compose.prod.yml pull
docker compose -f docker-compose.prod.yml up -d
```

---

## Native Deployment (systemd)

```bash
# Install
pip install pyra

# Run setup wizard (generates config + systemd unit file)
pybot-setup

# Install the generated service file
sudo cp config/pyra.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pyra

# Check status
sudo systemctl status pyra
pybot-ctl status
```

---

## nginx Reverse Proxy (Web UI)

```nginx
server {
    listen 80;
    server_name pyra.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name pyra.example.com;

    ssl_certificate /etc/letsencrypt/live/pyra.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/pyra.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

The WebSocket upgrade headers are required for the `/console/ws` endpoint.

---

## Database Migrations

Alembic handles schema migrations automatically on container start.

For manual migration management:

```bash
# Apply all pending migrations
alembic upgrade head

# Generate migration after model changes
alembic revision --autogenerate -m "add foo column"

# Roll back one migration
alembic downgrade -1

# Show current revision
alembic current
```

For PostgreSQL, set `DATABASE_URL` before running alembic:

```bash
export DATABASE_URL="postgresql+asyncpg://pyra:password@localhost:5432/pyra"
alembic upgrade head
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | (from config) | Override DB connection string |
| `DATA_DIR` | `/data` | Docker data directory |
| `CONFIG_FILE` | `$DATA_DIR/config.yaml` | Config file path |
| `PYRA_CONFIG` | `config/config.yaml` | Used by `pybot-ctl` |
| `PYRA_PID_FILE` | `data/pyra.pid` | PID file location |
| `PYRA_LOG` | `data/pyra.log` | Log file location |

---

## Volume Mounts (Docker)

| Container path | Purpose |
|----------------|---------|
| `/data` | Config, SQLite database, log files |
| `/plugins_extra` | User-installed plugins (hot-reloaded) |

---

## Security Checklist

- [ ] Change `web.secret_key` to a random 32+ character string
- [ ] Set a strong partyline/web password during `pybot-setup`
- [ ] Bind partyline to `127.0.0.1` (default) — never expose port 3333 publicly
- [ ] Use SSL/TLS for IRC connection (`ssl: true`, port 6697)
- [ ] Put web UI behind nginx with HTTPS if exposing publicly
- [ ] Keep `POSTGRES_PASSWORD` in `.env` (gitignored), not in compose file
- [ ] Set owner flag only for trusted hostmasks, not `*!*@*`
