# Plugin Development Guide

## Quick Start

Create a `.py` file in `plugins_extra/` (or `pybot/plugins/` for built-ins).
You can also do this from the Web UI Plugins page via Upload or Create Skeleton.

```python
from pybot import plugin

@plugin.command("ping", help="Respond with pong")
async def ping(bot, trigger):
    await bot.reply(trigger, "Pong!")
```

That's it - the file is loaded automatically on startup and on hot-reload.

In Web UI, extra plugins can also be edited directly from the plugin detail page:

- Edit Config Vars: saves to `plugins.vars.<plugin_name>` in `config.yaml`
- Script Editor: edits the plugin file for `plugins_extra/` entries

Admin auth note:

- Additional admins need a user password hash for Web UI/partyline login.
- Owner can add a user and auto-send credentials with `!adduser <nick!user@host> <flags>`.
- Owner can also set one manually with `!setpass <nick> <password>`.
- Admins can rotate their own with `!passwd <newpassword>`.
- Owner can bind services-account identity with `!useserviceauth` for stronger account-based auth.
- Admins can inspect and control scheduler tasks with `!jobs list`, `!jobs pause <plugin.func>`, and `!jobs resume <plugin.func>`.

CTCP/DCC compatibility:

- Built-in `ctcp` plugin adds CTCP replies (`VERSION`, `PING`, `TIME`, `CLIENTINFO`, `SOURCE`).
- DCC is policy-driven and denied by default with a helpful response.

---

## The `Trigger` Object

Every handler receives `(bot, trigger)`. The `Trigger` dataclass exposes:

| Attribute | Type | Description |
|-----------|------|-------------|
| `trigger.nick` | `str` | Sender's nick |
| `trigger.user` | `str` | Sender's ident |
| `trigger.host` | `str` | Sender's hostname |
| `trigger.hostmask` | `str` | Full `nick!user@host` |
| `trigger.account` | `str \| None` | NickServ account (if extended-join/account-notify) |
| `trigger.channel` | `str \| None` | Channel name, or `None` for PMs |
| `trigger.is_pm` | `bool` | True if message was a private message |
| `trigger.args` | `list[str]` | Tokenised command arguments |
| `trigger.match` | `re.Match \| None` | Regex match (for `@plugin.rule`) |
| `trigger.message` | `IRCMessage` | Raw IRC message object |
| `trigger.admin` | `bool` | Has `a` or `n` flag |
| `trigger.owner` | `bool` | Has `n` flag |

```python
async def has_flag(flag: str, channel: str | None = None) -> bool: ...
```

---

## Decorators

### `@plugin.command`

Triggered when a user sends `!<name>` (or whatever the command prefix is).

```python
@plugin.command(
    "weather",
    privilege=None,        # required flag: 'n','a','o','v', or None for all
    channels=None,         # list of channels to restrict to, or None for all
    aliases=["w"],         # alternative command names
    help="Show weather",   # shown in !help
    usage="!weather [set] <location>",
)
async def weather(bot, trigger):
    location = " ".join(trigger.args) or "Auckland"
    ...
```

### `@plugin.rule`

Triggered when a message matches a regex pattern.

```python
@plugin.rule(r"https?://\S+", priority=10)
async def url_titles(bot, trigger):
    url = trigger.match.group(0)
    ...
```

Higher `priority` values run first. Default is 0.

### `@plugin.event`

Triggered on a specific IRC command/numeric.

```python
@plugin.event("JOIN")
async def on_join(bot, trigger):
    await bot.say(trigger.channel, f"Welcome, {trigger.nick}!")

@plugin.event("PRIVMSG")
async def on_privmsg(bot, trigger):
    ...
```

### `@plugin.interval`

Runs periodically in the background. The function receives only `bot`.

```python
@plugin.interval(60)   # every 60 seconds
async def cleanup(bot):
    async with get_session() as session:
        await session.execute(...)
```

---

## Bot API

All `bot.*` methods are async:

```python
await bot.say(target, message)          # PRIVMSG
await bot.reply(trigger, message)       # PRIVMSG with "nick: " prefix
await bot.notice(target, message)       # NOTICE
await bot.action(target, message)       # CTCP ACTION (/me)
await bot.join(channel, key="")
await bot.part(channel, reason="")
await bot.kick(channel, nick, reason="")
await bot.ban(channel, hostmask)
await bot.unban(channel, hostmask)
await bot.op(channel, nick)
await bot.deop(channel, nick)
await bot.voice(channel, nick)
await bot.devoice(channel, nick)
await bot.topic(channel, text)
await bot.mode(target, modes, *args)
await bot.raw(line)                     # send raw IRC line (owner only)
await bot.whois(nick)                   # returns dict of WHOIS info
```

### Channel state

```python
channel = bot.get_channel("#general")     # ChannelState or None
nick_state = bot.get_nick_in_channel("#general", "alice")
```

---

## Database

Use `get_session()` for all DB access:

```python
from pybot.core.database import get_session, SeenEntry
from sqlalchemy import select

async def update_seen(nick, channel, message):
    async with get_session() as session:
        entry = SeenEntry(nick=nick, channel=channel, message=message)
        session.add(entry)
        await session.commit()
```

Available models: `User`, `UserFlag`, `Channel`, `ChannelSetting`, `Ban`, `Ignore`, `SeenEntry`, `Tell`, `Note`, `PluginSetting`, `Log`, `Reminder`, `ScheduledTask`, `Karma`.

Helper functions:

```python
from pybot.core.database import (
    get_or_create_channel,
    get_channel_setting,
    set_channel_setting,
    get_plugin_setting,
    set_plugin_setting,
)
```

---

## Plugin Config Vars (API Keys & Settings)

Static configuration — API keys, endpoints, feature flags — lives in `config.yaml` under `plugins.vars.<plugin_name>`. This section is in the runtime config file which is gitignored, so secrets stay out of source control.

**config.yaml:**
```yaml
plugins:
  vars:
    myplugin:
      api_key: "your-secret-key"
      endpoint: "https://api.example.com/v1"
      max_results: 5
```

**Reading in a plugin:**
```python
@plugin.command("query")
async def cmd_query(bot, trigger):
    cfg = bot.plugin_config("myplugin")
    api_key = cfg.get("api_key", "")
    if not api_key:
        await bot.reply(trigger, "No API key configured — set plugins.vars.myplugin.api_key in config.yaml")
        return
    endpoint = cfg.get("endpoint", "https://api.example.com/v1")
    max_results = int(cfg.get("max_results", 3))
    ...
```

`bot.plugin_config(name)` returns an empty dict `{}` if the plugin has no vars configured, so `.get("key", default)` is always safe.

---

## Plugin Settings

Store per-plugin configuration in the `plugin_settings` table (for *user-supplied* runtime data like saved locations, preferences):

```python
from pybot.core.database import get_session, get_plugin_setting, set_plugin_setting

async def get_location(nick):
    async with get_session() as session:
        return await get_plugin_setting(session, "weather", f"location:{nick}")

async def save_location(nick, location):
    async with get_session() as session:
        await set_plugin_setting(session, "weather", f"location:{nick}", location)
        await session.commit()
```

---

## Permissions

Check flags inside a handler using the `Trigger`:

```python
@plugin.command("secret")
async def secret(bot, trigger):
    if not await trigger.has_flag("o"):
        await bot.reply(trigger, "You need op flag for that.")
        return
    ...
```

Or use the `privilege` shorthand on the decorator:

```python
@plugin.command("admin_cmd", privilege="a")
async def admin_cmd(bot, trigger):
    ...  # only called if trigger.has_flag('a') is True
```

---

## Setup / Shutdown Hooks

```python
async def setup(bot):
    """Called once when plugin is loaded."""
    bot.memory["my_plugin_cache"] = {}

async def shutdown(bot):
    """Called when plugin is unloaded."""
    bot.memory.pop("my_plugin_cache", None)
```

### Plugins_extra Database Tables

For `plugins_extra/` plugins that need custom tables, define SQLAlchemy models in the plugin file and create them in `setup()` with `ensure_plugin_tables()`.

Do not add `plugins_extra/` tables to core Alembic migrations.

```python
from pybot.core.database import Base, ensure_plugin_tables
from sqlalchemy.orm import Mapped, mapped_column

class MyPluginEntry(Base):
    __tablename__ = "myplugin_entries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)

async def setup(bot):
    await ensure_plugin_tables(MyPluginEntry)
```

---

## Hot Reload

Send `SIGHUP` to reload all changed plugins:

```bash
pybot-ctl reload
# or from partyline:
!reload myplugin
# or reload all:
!reload *
```

The watchdog polls file mtimes every 5 seconds and auto-reloads changed plugins.

---

## Plugin Loading Order

1. `pybot/plugins/` — built-in plugins (always loaded unless disabled)
2. `plugins_extra/` — user plugins (loaded in alphabetical order)

To disable a plugin, add it to `plugins.disabled` in `config.yaml`:

```yaml
plugins:
  disabled:
    - search
```

---

## Full Example Plugin

```python
"""notes.py — personal note storage."""
from __future__ import annotations

from pybot import plugin
from pybot.core.database import get_session, Note
from sqlalchemy import select


@plugin.command("note", help="Manage personal notes", usage="!note <add|list|del|show> ...")
async def note(bot, trigger):
    if not trigger.args:
        await bot.reply(trigger, "Usage: !note <add|list|del|show> [text/id]")
        return

    sub = trigger.args[0].lower()

    if sub == "add":
        text = " ".join(trigger.args[1:])
        if not text:
            await bot.reply(trigger, "Usage: !note add <text>")
            return
        async with get_session() as session:
            note_obj = Note(nick=trigger.nick, hostmask=trigger.hostmask, content=text)
            session.add(note_obj)
            await session.commit()
            await bot.reply(trigger, f"Note #{note_obj.id} saved.")

    elif sub == "list":
        async with get_session() as session:
            rows = (await session.execute(
                select(Note).where(Note.nick == trigger.nick).order_by(Note.created_at)
            )).scalars().all()
        if not rows:
            await bot.reply(trigger, "No notes.")
        else:
            for n in rows[:5]:
                await bot.reply(trigger, f"[#{n.id}] {n.content[:80]}")
    else:
        await bot.reply(trigger, "Subcommands: add, list, del, show")
```
