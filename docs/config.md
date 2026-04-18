# Configuration Reference

Pyra is configured via a YAML file (default: `config/config.yaml`).  
Run `pybot-setup` to generate one interactively, or copy `config/config.example.yaml`.

---

## `core`

```yaml
core:
  nick: Pyra               # Primary IRC nick
  alt_nicks: [Pyra_, Pyra__]  # Fallbacks if nick is taken
  ident: pyra              # IRC ident (username)
  realname: "Pyra IRC Bot" # IRC real name
  command_prefix: "!"      # Prefix for commands (e.g. !help)
```

---

## `servers`

List of servers in priority order. The bot uses the highest-priority reachable server.

```yaml
servers:
  - host: irc.libera.chat
    port: 6697
    ssl: true
    priority: 1
    password: ""           # Server password (optional)
  - host: irc.rizon.net
    port: 6697
    ssl: true
    priority: 2
```

---

## `auth`

```yaml
auth:
  method: none             # none | nickserv | sasl_plain | sasl_external | sasl_scram
  nickserv_password: ""    # NickServ password (also used for sasl_plain/sasl_scram)
  sasl_mechanism: PLAIN    # PLAIN | EXTERNAL | SCRAM-SHA-256
```

---

## `channels`

```yaml
channels:
  autojoin:
    - "#general"
    - "#bots"
    - "#staff key123"      # Channel with key
```

---

## `database`

```yaml
database:
  # SQLite (default)
  url: "sqlite+aiosqlite:///data/pyra.db"

  # PostgreSQL
  # url: "postgresql+asyncpg://user:password@localhost:5432/pyra"

  pool_pre_ping: true      # Verify connections before use
  pool_recycle: 3600       # Recycle connections after 1 hour
  echo: false              # Log all SQL (debug only)
```

---

## `flood`

Controls the outbound IRC message rate limiter:

```yaml
flood:
  rate: 0.5                # Seconds between messages (token bucket refill rate)
  burst: 5                 # Maximum burst of messages
```

---

## `partyline`

```yaml
partyline:
  enabled: true
  host: "127.0.0.1"        # NEVER set to 0.0.0.0 unless behind a firewall
  port: 3333
```

---

## `web`

```yaml
web:
  enabled: true
  host: "0.0.0.0"
  port: 8080
  secret_key: ""   # Auto-generated and saved to config.yaml on first run — leave blank or set explicitly
```

---

## `plugins`

```yaml
plugins:
  dirs:
    - "pybot/plugins"      # Built-in plugins
    - "plugins_extra"      # User plugins
  disabled:
    - weather              # Disable specific built-ins
  autoload: true           # Load all plugins in dirs on startup
```

---

## `logging`

```yaml
logging:
  level: INFO              # DEBUG | INFO | WARNING | ERROR
  file: "data/pyra.log"    # Log file path (omit to disable file logging)
  rotation: "10 MB"        # Rotate when file reaches this size
  retention: "30 days"     # Keep rotated logs for this long
  compression: gz          # Compress rotated logs
```

---

## `services`

Anope integration (optional):

```yaml
services:
  enabled: false
  nickserv_nick: NickServ
  chanserv_nick: ChanServ
  memoserv_nick: MemoServ
```

---

## Environment Variables

These override config file values:

| Variable | Effect |
|----------|--------|
| `DATABASE_URL` | Override `database.url` (also used by Alembic) |
| `PYRA_CONFIG` | Path to config.yaml (used by `pybot-ctl`) |
| `PYRA_PID_FILE` | PID file path (used by `pybot-ctl`) |
| `PYRA_LOG` | Log file path (used by `pybot-ctl`) |

---

## Full Example

See `config/config.example.yaml` for a complete annotated example.
