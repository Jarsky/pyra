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
  auth_method: none        # none | sasl | nickserv | authserv | q | userserv | server_password
  nickserv_password: ""    # Service auth password (NickServ/AuthServ/Q/UserServ)
  sasl_mechanism: PLAIN    # PLAIN | EXTERNAL | SCRAM-SHA-256
  sasl_username: ""        # Optional account/login name for service auth commands
  nickserv_identify: false # Legacy alias for auth_method: nickserv
```

Notes:
- `auth_method: sasl` uses CAP/SASL negotiation and does not send a post-connect IDENTIFY.
- `auth_method: server_password` uses the server PASS value during registration.
- `auth_method: authserv`, `q`, and `userserv` send service-specific AUTH/LOGIN commands after 001.

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
  trusted_proxies:  # IP/CIDR list that can supply X-Forwarded-* headers
    - "127.0.0.1"
    - "::1"
```

Use `trusted_proxies` to explicitly control which reverse proxies are allowed
to set forwarded headers. Keep this list narrow to avoid header spoofing.

---

## `plugins`

```yaml
plugins:
  enabled: "all"                  # or list: ["help", "seen", "search"]
  disabled: []                     # explicit deny-list
  extra_dir: "/plugins_extra"     # Docker default; can be relative on bare metal
  vars:
    search:
      max_results: 3
    # Optional plugins in plugins_extra/
    # weather:
    #   units: metric
    # url:
    #   max_title_length: 200
    # headlines:
    #   include_global_feeds: true
    #   include_community_feeds: true
    #   default_feed: "reuters_world"
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
  chanserv_op: true
  channel_guard: false
  channel_guard_reinvite: true
  channel_guard_reop: true
  vhost: ""
  commands_on_connect: []  # Raw IRC lines queued after registration/auth
```

`commands_on_connect` is useful for advanced setups that need custom startup lines
without writing a plugin.

If `channel_guard` is enabled, Pyra can ask ChanServ to recover channel access for
the bot after a kick and when bot op mode is removed. Use `channel_guard_reinvite`
and `channel_guard_reop` to control those actions.

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
