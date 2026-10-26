"""
Runtime plugin choices, stored in the database so they survive restarts.

- Loading/unloading a plugin from IRC or the web UI is remembered and overrides
  plugins.enabled/plugins.disabled in config.yaml (which is never rewritten for this).
- Plugins can be switched off per channel.

Both use the plugin_settings table: key "state" (channel NULL) = "loaded"/"unloaded";
key "enabled" with a channel = "true"/"false".
"""

from __future__ import annotations

from sqlalchemy import select

from pybot.core.database import PluginSetting, get_session, set_plugin_setting

STATE_KEY = "state"
CHANNEL_KEY = "enabled"


async def set_plugin_state(name: str, loaded: bool) -> None:
    async with get_session() as session:
        await set_plugin_setting(session, name, STATE_KEY, "loaded" if loaded else "unloaded")


async def get_plugin_states() -> dict[str, bool]:
    """Plugins explicitly loaded (True) or unloaded (False) at runtime."""
    async with get_session() as session:
        result = await session.execute(
            select(PluginSetting).where(
                PluginSetting.key == STATE_KEY, PluginSetting.channel.is_(None)
            )
        )
        return {row.plugin: row.value == "loaded" for row in result.scalars().all()}


async def set_channel_enabled(name: str, channel: str, enabled: bool) -> None:
    async with get_session() as session:
        await set_plugin_setting(
            session, name, CHANNEL_KEY, "true" if enabled else "false", channel=channel.lower()
        )


async def get_channel_disabled() -> set[tuple[str, str]]:
    """(plugin, lowercased channel) pairs where a plugin is switched off."""
    async with get_session() as session:
        result = await session.execute(
            select(PluginSetting).where(
                PluginSetting.key == CHANNEL_KEY,
                PluginSetting.channel.is_not(None),
                PluginSetting.value == "false",
            )
        )
        return {(row.plugin, (row.channel or "").lower()) for row in result.scalars().all()}
