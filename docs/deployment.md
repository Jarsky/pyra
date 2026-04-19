# Deployment Guide

## Docker (SQLite)

Suitable for single-server setups, low to medium traffic.

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra/docker

# Start — config.yaml is created automatically from the example on first run
docker compose up -d

# Edit the generated config (server, nick, channels, etc.), then restart
$EDITOR ../data/config.yaml
docker compose restart pyra

# Check logs
docker compose logs -f pyra
```

The SQLite database is stored in `data/pyra.db`.

---

## Docker (PostgreSQL)

Recommended for production deployments.

```bash
cd pyra/docker

# Set the database password (only required secret)
cp .env.example .env
$EDITOR .env

# Start — config.yaml is created automatically from the example on first run
docker compose -f docker-compose.prod.yml up -d

# Edit the generated config (server, nick, channels, etc.), then restart
$EDITOR ../data/config.yaml
docker compose -f docker-compose.prod.yml restart pyra
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

# Set DB password (prod)
cp .env.example .env
nano .env

# Start — config.yaml is created automatically on first run
docker compose -f docker-compose.prod.yml up -d

# Edit the generated config (server, nick, channels, etc.), then restart
nano ../data/config.yaml
docker compose -f docker-compose.prod.yml restart pyra
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

## Reverse Proxy Setup (HTTPS)

If you expose the Pyra web UI publicly, run it behind a reverse proxy and terminate TLS there.

### Pyra config changes (required)

Set these values in `config.yaml`:

```yaml
web:
    enabled: true
    host: "127.0.0.1"
    port: 8080
    trusted_proxies:
        - "127.0.0.1"
```

Notes:

- Use `web.host: "127.0.0.1"` when proxy and Pyra run on the same host.
- Use `web.host: "0.0.0.0"` if Pyra runs in Docker and your proxy is in another container/network.
- Add your proxy source IP/CIDR to `web.trusted_proxies` so forwarded client IP headers are trusted.

### Nginx (HTTPS)

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
                proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_http_version 1.1;
                proxy_set_header Upgrade $http_upgrade;
                proxy_set_header Connection "upgrade";
        }
}
```

The upgrade headers are required for the `/console/ws` WebSocket endpoint.

### Caddy (HTTPS)

```caddy
pyra.example.com {
        reverse_proxy 127.0.0.1:8080
}
```

Caddy automatically provisions TLS and forwards standard proxy headers.

### Traefik (HTTPS)

File provider example (`dynamic/pyra.yml`):

```yaml
http:
    routers:
        pyra:
            rule: Host(`pyra.example.com`)
            entryPoints:
                - websecure
            tls:
                certResolver: letsencrypt
            service: pyra

    services:
        pyra:
            loadBalancer:
                servers:
                    - url: http://127.0.0.1:8080
```

Static Traefik config must define:

- `entryPoints.web` on `:80`
- `entryPoints.websecure` on `:443`
- an ACME resolver named `letsencrypt`

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
