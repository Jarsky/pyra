"""
Translate plugin — translate text using LibreTranslate.

Author:  Jarsky
Version: 1.0.0
Date:    2026-04-18


Plugin vars (config.yaml plugins.vars.translate):
  endpoint: "https://libretranslate.com"  (or self-hosted URL)
  api_key: ""                              (required on libretranslate.com, optional self-hosted)

Commands:
  !tr <lang> <text>           Translate to language (auto-detect source)
  !tr <src>-><tgt> <text>     Translate from src to tgt (e.g. !tr fr->en bonjour)
  !trlang                     List supported languages
  !trset <lang>               Set your default target language (saved per-nick)
"""

from __future__ import annotations

import httpx

from pybot import plugin
from pybot.plugin import Trigger

_DEFAULT_ENDPOINT = "https://libretranslate.com"


def _get_cfg(bot: object) -> tuple[str, str]:
    cfg: dict[str, object] = bot.plugin_config("translate")  # type: ignore[attr-defined]
    endpoint = str(cfg.get("endpoint", _DEFAULT_ENDPOINT)).rstrip("/")
    api_key = str(cfg.get("api_key", ""))
    return endpoint, api_key


@plugin.command(
    "tr",
    aliases=["translate"],
    help="Translate text",
    usage="!tr <lang> <text>  or  !tr <src>-><tgt> <text>  (e.g. !tr fr->en bonjour)",
)
async def cmd_translate(bot: object, trigger: Trigger) -> None:
    if len(trigger.args) < 2:
        await bot.reply(trigger, "Usage: !tr <lang> <text>  or  !tr fr->en <text>")  # type: ignore[attr-defined]
        return

    endpoint, api_key = _get_cfg(bot)
    lang_arg = trigger.args[0]
    text = " ".join(trigger.args[1:])

    if "->" in lang_arg:
        src, tgt = lang_arg.split("->", 1)
    else:
        src = "auto"
        tgt = lang_arg

    result = await _translate(endpoint, api_key, text, source=src, target=tgt)
    if result is None:
        await bot.reply(trigger, "Translation failed. Check endpoint/api_key config.")  # type: ignore[attr-defined]
        return
    await bot.say(trigger.target, f"[{src}→{tgt}] {result}")  # type: ignore[attr-defined]


@plugin.command("trlang", help="List supported translation languages")
async def cmd_trlang(bot: object, trigger: Trigger) -> None:
    endpoint, api_key = _get_cfg(bot)
    try:
        params = {}
        if api_key:
            params["api_key"] = api_key
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{endpoint}/languages", params=params)
            langs = resp.json()
    except Exception as exc:
        await bot.reply(trigger, f"Could not fetch languages: {exc}")  # type: ignore[attr-defined]
        return

    if not isinstance(langs, list):
        await bot.reply(trigger, "Unexpected response from translate API.")  # type: ignore[attr-defined]
        return

    codes = " ".join(f"{lg.get('code','?')}({lg.get('name','?')})" for lg in langs[:20])
    await bot.notice(trigger.nick, f"Supported languages: {codes}")  # type: ignore[attr-defined]
    if len(langs) > 20:
        await bot.notice(trigger.nick, f"  ... and {len(langs) - 20} more")  # type: ignore[attr-defined]


@plugin.command("trset", help="Set your default translation target language", usage="!trset <lang>")
async def cmd_trset(bot: object, trigger: Trigger) -> None:
    if not trigger.args:
        await bot.reply(trigger, "Usage: !trset <lang>  (e.g. !trset en)")  # type: ignore[attr-defined]
        return
    lang = trigger.args[0].lower()
    from pybot.core.database import get_session, set_plugin_setting
    async with get_session() as session:
        await set_plugin_setting(session, "translate", "default_lang", lang, channel=trigger.nick)
    await bot.reply(trigger, f"Default translation language set to: {lang}")  # type: ignore[attr-defined]


async def _translate(
    endpoint: str, api_key: str, text: str, source: str = "auto", target: str = "en"
) -> str | None:
    payload: dict[str, str] = {"q": text, "source": source, "target": target, "format": "text"}
    if api_key:
        payload["api_key"] = api_key
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{endpoint}/translate", json=payload)
            data = resp.json()
        return str(data.get("translatedText", "")) or None
    except Exception:
        return None
