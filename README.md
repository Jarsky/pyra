<p align="center">
  <img src="./assets/images/PyraBanner.png" alt="Pyra - Python IRC Bot" />
</p>

# Pyra — Modern Python IRC Bot

<p align="center">
  <b>The IRC bot that is easy to start, simple to run, and powerful when you need it.</b><br>
  Start in minutes with Docker, then grow into advanced automation, moderation, and admin workflows.
</p>

![CI](https://img.shields.io/github/actions/workflow/status/Jarsky/pyra/ci.yml?branch=main)
![License](https://img.shields.io/github/license/Jarsky/pyra)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)

---

## Why Pyra

- **Fast start, low setup stress** - run with Docker and get a working bot quickly.
- **Beginner-friendly, power-user ready** - great defaults for simple bots, deep controls when you need them.
- **Flexible plugin system** - use built-ins now, add your own custom commands later.
- **Modern web admin panel** - manage channels, users, plugins, and logs from your browser.
- **Reliable for real communities** - moderation tools, permissions, and production deployment support.
- **Runs where you want** - SQLite by default, PostgreSQL optional, native or container deployment.

## What You Get Out Of The Box

- **15 built-in plugins** for moderation, utility, notes, search, seen/tell, uptime, and more.
- **Optional extra plugins** in `plugins_extra/` including headlines, weather, URL tools, and media helpers.
- **Strong IRC support** including IRCv3, TLS, SASL auth, and connection flood protection.
- **Live admin workflows** with both a web UI and partyline console.
- **Hot-reload support** so plugin updates do not require full bot restarts.

---

## Screenshots

| Web UI | Partyline |
|--------|-----------|
| ![Web UI screenshot](./assets/screenshots/WebUI.png) | ![Partyline screenshot](./assets/screenshots/PartyLIne.png) |

| Channels Admin | Console |
|----------------|---------|
| ![Channels Admin screenshot](./assets/screenshots/ChannelsAdmin.png) | ![Web Console screenshot](./assets/screenshots/WebConsole.png) |

---

## Quick Start

### Basic Docker (recommended)

Linux/macOS:

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra

# Start (creates data/config.yaml on first run)
docker compose -f docker/docker-compose.yml up -d

# Edit config, then restart
nano data/config.yaml
docker compose -f docker/docker-compose.yml restart pyra
```

Windows PowerShell:

```powershell
git clone https://github.com/Jarsky/pyra.git
cd pyra

# Start (creates data/config.yaml on first run)
docker compose -f docker/docker-compose.yml up -d

# Edit config, then restart
notepad data/config.yaml
docker compose -f docker/docker-compose.yml restart pyra
```

### Native install

Linux/macOS:

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra

python -m venv venv
source venv/bin/activate
pip install -e "."

# Interactive setup wizard
pybot-setup

# Start the bot
pybot --config config/config.yaml
```

Windows PowerShell:

```powershell
git clone https://github.com/Jarsky/pyra.git
cd pyra

python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -e "."

# Interactive setup wizard
pybot-setup

# Start the bot
pybot --config config/config.yaml
```

### Advanced Docker (PostgreSQL)

Linux/macOS:

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra

# Set the database password (only required secret)
cp docker/.env.example docker/.env
nano docker/.env

# Start (creates data/config.yaml on first run)
docker compose -f docker/docker-compose.prod.yml up -d

# PostgreSQL DB selection note:
# docker-compose.prod.yml sets DATABASE_URL for PostgreSQL automatically,
# so you do NOT need to change database.url in config.yaml for this stack.

# Edit the generated config (server, nick, channels, etc.), then restart
nano data/config.yaml
docker compose -f docker/docker-compose.prod.yml restart pyra
```

Windows PowerShell:

```powershell
git clone https://github.com/Jarsky/pyra.git
cd pyra

# Set the database password (only required secret)
Copy-Item docker/.env.example docker/.env
notepad docker/.env

# Start (creates data/config.yaml on first run)
docker compose -f docker/docker-compose.prod.yml up -d

# Edit config, then restart
notepad data/config.yaml
docker compose -f docker/docker-compose.prod.yml restart pyra
```

If you want MariaDB/MySQL instead, set the DB URL yourself (in config or env):

```yaml
database:
  url: "mysql+aiomysql://pyra:password@db-host/pyra"
```

Or with environment override:

```bash
DATABASE_URL="mysql+aiomysql://pyra:password@db-host/pyra" pybot --config config/config.yaml
```

---

## Configuration

Run the setup wizard to generate your config:

```bash
pybot-setup
```

Or copy and edit the example manually:

Linux/macOS:

```bash
cp config/config.example.yaml config/config.yaml
nano config/config.yaml
```

Windows PowerShell:

```powershell
Copy-Item config/config.example.yaml config/config.yaml
notepad config/config.yaml
```

See [docs/config.md](docs/config.md) for full reference.

---

## Plugin Development

Pyra plugins are regular Python files dropped into `plugins_extra/`, with decorator-based hooks for commands, regex rules, and scheduled tasks.

See [docs/plugins.md](docs/plugins.md) for the full plugin API, config examples, and plugin-specific settings under `plugins.vars`.

---

## Permissions

Pyra uses Eggdrop-style flag-based ACL with global and per-channel overrides for owners, admins, ops, voice, ignores, and antispam exemptions.

See [docs/permissions.md](docs/permissions.md) for full reference.

---

## Web Interface

The web UI runs on port `8080` by default:

- **Default login** — on first run, owner login is bootstrapped from config:
  `username = core.owner`, `password = partyline.password`
- **Additional admin logins** — after adding an admin user/flags, set their login password from IRC:
  `!adduser <nick!user@host> <flags>` (auto-generates and /msgs credentials),
  `!setpass <nick> <password>` (owner override), or
  `!passwd <newpassword>` (self-service for admins)

- **Dashboard** — uptime, channels, and recent activity
- **Channels** — settings and moderation controls
- **Users** — user management and flags
- **Plugins** — load/unload/reload, upload new plugin files, create skeleton plugins, edit vars, and edit extra plugin scripts
- **Logs / Console / Settings** — operational admin tools in one place

---

## Partyline

Connect via telnet for a live admin console:

```bash
telnet 127.0.0.1 3333
# or
pybot-ctl console
```

Login credentials are the same as web by default:
`username = core.owner`, `password = partyline.password`.

For non-owner admins, credentials are the same account/password stored in the user DB
(`!setpass` / `!passwd` commands).

Partyline provides a live admin console with bot controls, channel operations, and a real-time IRC event stream.

---

## pybot-ctl

Daemon manager for native deployments:

```bash
pybot-ctl start              # Start the bot
pybot-ctl stop               # Graceful shutdown
pybot-ctl restart            # Stop + start
pybot-ctl status             # PID status
pybot-ctl reload             # Hot-reload all plugins (SIGHUP)
pybot-ctl logs -f            # Follow log output
pybot-ctl console            # Connect to partyline
```

---

## Deployment

See [docs/deployment.md](docs/deployment.md) for:

- Docker stack on `host.domain`
- PostgreSQL production setup
- Systemd service configuration
- nginx reverse proxy for web UI
- SSL certificate setup

---

## Documentation

For end-user and operator guides (beginner through advanced), use the GitHub Wiki:

- [Pyra Wiki](https://github.com/Jarsky/pyra/wiki)

- [Configuration reference](docs/config.md)
- [Deployment guide](docs/deployment.md)
- [Permissions reference](docs/permissions.md)
- [Plugin API and configuration](docs/plugins.md)

---

## Development

```bash
pip install -e ".[dev]"

# Lint + format
ruff check pybot/
black pybot/

# Type check
mypy pybot/

# Tests
pytest tests/ --cov=pybot

# DB migrations
alembic upgrade head
alembic revision --autogenerate -m "describe change"
```

---

## Built-in Plugins

Included built-in plugins cover administration, moderation, antispam, utilities, notes, offline tells, and search.
Optional plugins in `plugins_extra/` include URL titles, weather, headlines, and additional integrations.

See [docs/plugins.md](docs/plugins.md) for plugin details and [plugins_extra/OPTIONAL_PLUGINS.md](plugins_extra/OPTIONAL_PLUGINS.md) for optional extras.

---

## License

MIT — see [LICENSE](LICENSE).
