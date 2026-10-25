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
| `k` | Autokick: kickbanned on join (also works as a global flag) |
| `b` | Banned from this channel: kickbanned on join |

### Channel enforcement

The built-in `changuard` plugin acts in channels where the bot has ops:

- **On join**: a stored ban (from `!ban`, `!kickban`, `!tempban`, antispam) that matches, or the
  `k`/channel `b` flag, gets the user banned and kicked. Owners are never kicked; users with `o`,
  `a` or `n` are exempt from stored bans.
- **Auto-op / auto-voice**: with the channel setting `autoop` (or `autovoice`) on, users with
  the `o` (or `v`) flag, global or for that channel, get +o (or +v) when they join.
- **When the bot is opped**, it applies the same checks to everyone already in the channel.
- **Ban expiry**: timed bans are lifted every 30 seconds, also after a restart.

Turn these on in the web UI (**Channels** → **Settings**) or with
`!chanset autoop true`, `!chanset autovoice true`, `!chanset enforce_bans false`.

---

## Flag Resolution Order

A user's flags are collected from **every** record that matches them, by hostmask pattern or
by services account. Global flags apply everywhere; channel flags add to them in that channel.

1. Owner `n` → implies all flags everywhere, and owners are never ignored
2. `I` (Ignore), or a matching `!ignore` entry → the bot ignores all their messages
3. Otherwise a check passes if the flag is among the collected flags

---

## Hostmask Matching

Patterns use IRC wildcards: `*` (any run of characters) and `?` (one character), compared
case-insensitively. Everything else is literal, including `[ ]` in nicks.

```
*!*@*                   matches anyone
nick!*@*                matches by nick only: anyone can take a nick (refused for n/a)
*!user@*.example.com    matches by ident + domain
*!*@192.168.1.*         matches IP subnet
nick!user@host          exact match
```

Flags from all matching patterns are combined, so a broad pattern (`*!*@*.example.com`)
grants its flags to everyone it matches.

---

## Managing Flags

### Via IRC commands

```
!adduser <nick!user@host> <flags> [name]  Create a user with global flags (owner)
!deluser <nick>                     Remove a user record (owner)
!flags <nick|mask>                  Show global flags (admin)
!flags <nick|mask> +ov-k            Add/remove global flags (admin)
!flags <nick|mask> #channel +o      Add/remove channel flags (admin)
!whois <nick>                       Show a user's record and flags (admin)
!ignore <mask> [duration]           Ignore a hostmask, e.g. 2h or 1d (admin)
```

`!adduser` needs a name when the mask's nick is a wildcard (`!adduser *!bad@spam.host k spammer`)
and generates a web/partyline login only for `a`/`n` users. Use the mask instead of the nick
when several users share a nick. Admins can't grant or
remove `n` or change an owner's flags, and a change with a refused flag is not applied at all.

### Via web UI

Navigate to **Users** → find the user → click **Edit Flags**.

### Via partyline

Not yet: the partyline has bot controls (`.say`, `.join`, `.reload`, ...) but no user commands.

---

## Initial Owner Setup

The first owner is created by `pybot-setup`, or at startup from `core.owner`,
`core.owner_account`, `core.owner_hostmask` and `partyline.password`.

### Identity rules

- A hostmask that only pins the nick (`nick!*@*`, `*!*@*`) identifies nobody: anyone can take
  that nick. Owner (`n`) and admin (`a`) flags are refused for such masks unless the user has
  a services account bound, and an existing owner record with one is cleared at startup.
- Prefer services accounts (`core.owner_account`, or `!useserviceauth` once you are
  recognised), or a mask that pins user and host, e.g. `*!jarsky@trusted.host.com`.
- Only owners can grant or remove the `n` flag or change an owner's flags, and only owners
  can delete users. Owners are never ignored.
- The web UI and partyline accept only users with the `a` or `n` flag. Losing the flag ends an
  existing web session on the next request.

To add another owner:

```
!adduser *!jarsky@trusted.host.com n
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

# Kickban a user whenever they join #general
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
