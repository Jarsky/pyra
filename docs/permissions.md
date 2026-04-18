# Permissions & Flag System

Pyra uses an Eggdrop-style flag-based ACL. Each user has a hostmask pattern (e.g. `nick!user@host.example.com`) and a set of flags.

---

## Flags

### Global Flags

| Flag | Name | Capabilities |
|------|------|-------------|
| `n` | Owner | Full control — implies all other flags |
| `a` | Admin | Manage bot, load/unload plugins, add/remove users |
| `o` | Op | Channel moderation commands |
| `v` | Voice | Trusted user, some restricted commands |
| `b` | Bot | Peer bot — some special treatment |
| `I` | Ignore | All commands rejected, no responses |
| `X` | Exempt | Bypasses antispam/flood detection |

### Channel Flags

Applied per-channel (e.g. `#general+o`):

| Flag | Meaning |
|------|---------|
| `o` | Channel op — moderation in this channel |
| `v` | Voice — trusted in this channel |
| `k` | Autokick on join |
| `b` | Banned from channel |

---

## Flag Resolution Order

1. Owner `n` → implies all flags everywhere
2. Global `I` (Ignore) → blocks all commands regardless of other flags
3. Global flags (apply everywhere)
4. Channel-specific flags (override globals for that channel)
5. NickServ account match (if services integration enabled)

---

## Hostmask Matching

Patterns use `fnmatch` wildcards:

```
*!*@*                   matches anyone
nick!*@*                matches by nick only (not recommended)
*!user@*.example.com    matches by ident + domain
*!*@192.168.1.*         matches IP subnet
nick!user@host          exact match
```

The most specific matching pattern wins.

---

## Managing Flags

### Via IRC commands (requires admin flag)

```
!adduser <nick> <hostmask>        Create a user record
!deluser <nick>                   Remove a user record
!flags <nick> +<flags>            Add global flags
!flags <nick> -<flags>            Remove global flags
!flags <nick> #channel +<flags>   Add channel flags
!flags <nick> #channel -<flags>   Remove channel flags
!whois <nick>                     Show user flags and info
```

### Via web UI

Navigate to **Users** → find the user → click **Edit Flags**.

### Via partyline

Same commands as IRC, no prefix needed.

---

## Initial Owner Setup

The first owner is created by `pybot-setup`. To add another owner:

```
!adduser jarsky *!jarsky@trusted.host.com
!flags jarsky +n
```

Or directly in the database:

```python
from pybot.core.database import get_session
from pybot.core.permissions import add_owner_bootstrap
import asyncio

async def main():
    async with get_session() as session:
        await add_owner_bootstrap(session, "jarsky", "*!jarsky@host.com", hashed_pw)
        await session.commit()

asyncio.run(main())
```

---

## Examples

```
# Make alice an admin globally
!flags alice +a

# Give bob op in #general only
!flags bob #general +o

# Ignore spambot (all commands rejected)
!flags spambot +I

# Exempt trusted bot from antispam
!flags goodbot +X

# Ban nick from #general via channel flag
!flags badnick #general +b
```

---

## Plugin Privilege Checks

Plugins can restrict commands to specific flags:

```python
# Require admin flag
@plugin.command("reload", privilege="a")
async def reload_cmd(bot, trigger):
    ...

# Manual check
@plugin.command("op")
async def op_cmd(bot, trigger):
    if not await trigger.has_flag("o", trigger.channel):
        await bot.reply(trigger, "You need op flag in this channel.")
        return
    ...
```
