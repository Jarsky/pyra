<p align="center">
  <img src="./assets/images/PyraBanner.png" alt="Pyra - Python IRC Bot" />
</p>

# Pyra — Modern Python IRC Bot

<p align="center">
  <b>A powerful, extensible, and production-ready IRC bot built with modern Python.</b><br>
  Inspired by Sopel and Eggdrop. Built with asyncio, FastAPI, SQLAlchemy, and HTMX.
</p>

![CI](https://img.shields.io/github/actions/workflow/status/Jarsky/pyra/ci.yml?branch=main)
![License](https://img.shields.io/github/license/Jarsky/pyra)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)

---

## Features

- **Full IRC/IRCv3 support** — TLS, SASL (PLAIN/EXTERNAL/SCRAM-SHA-256), CAP negotiation, flood protection
- **Async throughout** — single asyncio event loop, no threads
- **Plugin system** — decorator-based API, hot reload via SIGHUP or watchdog
- **15 built-in plugins** — admin, antispam, calc, choose, dice, greet, help, notes, search, seen, tell, uptime, url, weather, adminchannel
- **Eggdrop-style permissions** — `n/a/o/v/b/I/X` flags, per-channel overrides, hostmask wildcards
- **Web admin UI** — FastAPI + Jinja2 + HTMX, no Node/React required
- **Partyline** — telnet admin console with multi-user chat, live IRC stream
- **Database** — SQLAlchemy 2.x async, SQLite default, PostgreSQL optional
- **Docker-ready** — Dockerfile, Compose files for both SQLite and PostgreSQL variants

---

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra/docker

# Copy and edit the config
mkdir -p ../data
cp ../config/config.example.yaml ../data/config.yaml
$EDITOR ../data/config.yaml

# Start
docker compose up -d
```

### Native install

```bash
git clone https://github.com/Jarsky/pyra.git
cd pyra

python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -e "."

# Interactive setup wizard
pybot-setup

# Start the bot
pybot --config config/config.yaml
```

### Production (PostgreSQL + Docker)

```bash
cd pyra/docker

# Ensure runtime config exists in ../data
mkdir -p ../data
cp -n ../config/config.example.yaml ../data/config.yaml
$EDITOR ../data/config.yaml

# Create your .env file with the DB password (gitignored)
cp .env.example .env
$EDITOR .env   # set POSTGRES_PASSWORD

docker compose -f docker-compose.prod.yml up -d
```

---

## Configuration

Run the setup wizard to generate your config:

```bash
pybot-setup
```

Or copy and edit the example manually:

```bash
cp config/config.example.yaml config/config.yaml
$EDITOR config/config.yaml
```

See [docs/config.md](docs/config.md) for full reference.

---

## Plugin API

Writing a plugin is minimal:

```python
from pybot import plugin

@plugin.command("hello", help="Say hello")
async def hello(bot, trigger):
    await bot.reply(trigger, f"Hello, {trigger.nick}!")

@plugin.rule(r"(?i)good(morning|night)")
async def greet_time(bot, trigger):
    await bot.say(trigger.channel, f"Hey {trigger.nick}!")

@plugin.interval(300)
async def periodic_task(bot):
    pass  # runs every 5 minutes
```

Drop `.py` files into `plugins_extra/` — they load automatically.

### Plugin API keys & settings

API keys and per-plugin settings live in `config.yaml` under `plugins.vars` — gitignored, so secrets never touch source control:

```yaml
# config/config.yaml
plugins:
  vars:
    myplugin:
      api_key: "your-secret-key"
      max_results: 5
```

Read them in your plugin with `bot.plugin_config("myplugin")`:

```python
@plugin.command("query")
async def cmd_query(bot, trigger):
    cfg = bot.plugin_config("myplugin")
    api_key = cfg.get("api_key", "")
    ...
```

Returns `{}` if no vars are configured, so `.get("key", default)` is always safe.

See [docs/plugins.md](docs/plugins.md) for full API reference.

---

## Permissions

Pyra uses Eggdrop-style flag-based ACL:

| Flag | Scope | Meaning |
|------|-------|---------|
| `n` | global | Owner — full control |
| `a` | global | Admin — manage bot |
| `o` | global/channel | Op — moderation |
| `v` | global/channel | Voice — trusted user |
| `b` | global | Bot — peer bot |
| `I` | global | Ignore — all commands rejected |
| `X` | global/channel | Exempt — bypasses antispam |

```bash
# In channel or partyline:
!adduser nick *!user@host.example.com
!flags nick +a
!flags nick #channel +o
```

See [docs/permissions.md](docs/permissions.md) for full reference.

---

## Web Interface

The web UI runs on port `8080` by default:

- **Dashboard** — live uptime, channels, recent logs
- **Channels** — per-channel settings (greet, antispam, flood control)
- **Users** — user management, flag assignment
- **Plugins** — load/unload/reload plugins
- **Logs** — searchable, filterable IRC log viewer
- **Console** — WebSocket terminal (equivalent to partyline)
- **Settings** — YAML config editor (owner only)

---

## Partyline

Connect via telnet for a live admin console:

```bash
telnet 127.0.0.1 3333
# or
pybot-ctl console
```

Commands: `!say #chan msg`, `!join #chan`, `!part #chan`, `!reload plugin`, `who`, `channels`, `!raw <irc>` (owner), `shutdown` (owner).

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

| Plugin | Commands | Description |
|--------|----------|-------------|
| `admin` | `!join !part !quit !reload !say !raw !adduser !deluser !flags !ignore` | Bot administration |
| `adminchannel` | `!op !deop !voice !devoice !kick !ban !unban !kickban !topic !mode` | Channel moderation |
| `antispam` | — | Auto flood/caps/repeat detection |
| `calc` | `!calc !c` | Safe math evaluator |
| `choose` | `!choose !8ball` | Random choice / magic 8-ball |
| `dice` | `!roll !rand` | NdM+K dice roller |
| `greet` | `!greet` | Per-channel join greeting |
| `help` | `!help` | Command reference |
| `notes` | `!note` | Personal note storage |
| `search` | `!search !wiki !define` | DuckDuckGo / Wikipedia search |
| `seen` | `!seen` | Nick last-seen tracker |
| `tell` | `!tell` | Offline message delivery |
| `uptime` | `!uptime` | Bot uptime display |
| `url` | — | Auto URL title fetching |
| `weather` | `!weather !forecast` | Weather via Open-Meteo |

---

## License

MIT — see [LICENSE](LICENSE).
