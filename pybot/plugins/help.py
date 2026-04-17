"""Help plugin — list commands and show usage information."""

from __future__ import annotations

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command("help", help="List available commands or show help for a specific command")
async def cmd_help(bot: object, trigger: Trigger) -> None:
    """Show command list or help for a specific command."""
    from pybot.plugin import get_registry

    registry = get_registry()

    if trigger.args:
        # Help for a specific command
        cmd_name = trigger.args[0].lstrip("!")
        handlers = registry.commands.get(cmd_name.lower(), [])
        if not handlers:
            await bot.notice(trigger.nick, f"No help found for '{cmd_name}'.")  # type: ignore[attr-defined]
            return
        h = handlers[0]
        lines = [f"\x02{cmd_name}\x02"]
        if h.help_text:
            lines.append(h.help_text)
        if h.usage:
            lines.append(f"Usage: {h.usage}")
        if h.privilege:
            lines.append(f"Requires flag: {h.privilege}")
        for line in lines:
            await bot.notice(trigger.nick, line)  # type: ignore[attr-defined]
    else:
        # Full command list — send as NOTICE to avoid channel flood
        commands = sorted(registry.commands.keys())
        if not commands:
            await bot.notice(trigger.nick, "No commands are currently loaded.")  # type: ignore[attr-defined]
            return

        prefix = bot.config.core.command_prefix  # type: ignore[attr-defined]
        chunks: list[str] = []
        current = ""
        for cmd in commands:
            entry = f"{prefix}{cmd}"
            if len(current) + len(entry) + 2 > 400:
                chunks.append(current.strip())
                current = entry
            else:
                current += f"  {entry}" if current else entry
        if current:
            chunks.append(current.strip())

        await bot.notice(trigger.nick, "Available commands:")  # type: ignore[attr-defined]
        for chunk in chunks:
            await bot.notice(trigger.nick, chunk)  # type: ignore[attr-defined]
        await bot.notice(trigger.nick, f"Use '{prefix}help <command>' for details.")  # type: ignore[attr-defined]
