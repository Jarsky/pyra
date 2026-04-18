"""
IP Info plugin — IP address geolocation lookup.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18

Provides commands to look up IP address geographic and ISP information.
Uses ip-api.com (free, no key required).

Commands:
  !ip <address>       Look up IP address geolocation and ISP info
  !ipinfo <address>   Alias for !ip
"""

from __future__ import annotations

__plugin_meta__ = {
    "author": "Jarsky",
    "version": "1.0.0",
    "updated": "2026-04-18",
    "description": "IP address geolocation and ISP lookup via ip-api.com. No API key required.",
    "url": "https://github.com/Jarsky/pyra",
}

import ipaddress

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_IPINFO_API = "http://ip-api.com/json"


def _is_valid_ip(ip_str: str) -> bool:
    """Validate IP address format."""
    try:
        ipaddress.ip_address(ip_str)
        return True
    except ValueError:
        return False


async def _lookup_ip(ip_addr: str, timeout: float = 8.0) -> dict[str, object] | None:
    """Fetch IP info from ip-api.com."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{_IPINFO_API}/{ip_addr}")
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != "success":
                return None
            return data
    except Exception:
        return None


@plugin.command(
    "ip",
    aliases=["ipinfo"],
    help="Look up IP address geolocation",
    usage="!ip <address>",
)
async def cmd_ip(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !ip <address>")  # type: ignore[attr-defined]
        return

    ip_addr = trigger.args[0]

    if not _is_valid_ip(ip_addr):
        await bot.reply(trigger, f"Invalid IP address: {ip_addr}")  # type: ignore[attr-defined]
        return

    data = await _lookup_ip(ip_addr)
    if not data:
        await bot.say(  # type: ignore[attr-defined]
            trigger.target, f"\x0304Error: Could not look up {ip_addr}"
        )
        return

    # Extract fields
    country = data.get("country", "Unknown")
    country_code = data.get("countryCode", "?")
    region = data.get("region", "Unknown")
    city = data.get("city", "Unknown")
    zip_code = data.get("zip", "Unknown")
    org = data.get("org", "Unknown")
    isp = data.get("isp", "Unknown")

    # Format output
    await bot.say(
        trigger.target,
        f"\x0307IP:\x03 {ip_addr} \x0311|\x03 \x0307Country:\x03 {country} "
        f"({country_code}) \x0311|\x03 \x0307City:\x03 {city}, {region} "
        f"\x0311|\x03 \x0307Zip:\x03 {zip_code}",
    )  # type: ignore[attr-defined]
    await bot.say(
        trigger.target,
        f"\x0307ISP:\x03 {isp} \x0311|\x03 \x0307Org:\x03 {org}",
    )  # type: ignore[attr-defined]
