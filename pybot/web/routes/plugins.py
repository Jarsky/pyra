"""Plugins management route."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote_plus

import yaml
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.core.config import save_config_partial
from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


def _plugin_meta(loader: Any, name: str) -> dict[str, str]:
    """Return __plugin_meta__ from a loaded module, or empty dict."""
    module = loader.get_module(name) if loader else None
    return getattr(module, "__plugin_meta__", {}) if module else {}


def _plugin_class(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_")) + "Plugin"


def _resolve_extra_dir(bot: Any) -> Path:
    extra = Path(bot.config.plugins.extra_dir)
    if not extra.is_absolute():
        return Path.cwd() / extra
    return extra


def _resolve_config_path() -> Path:
    configured = os.environ.get("CONFIG_FILE")
    if configured:
        return Path(configured)

    docker_default = Path("/data/config.yaml")
    if docker_default.exists() or Path("/.dockerenv").exists():
        return docker_default

    return Path("config/config.yaml")


async def _is_owner(bot: Any, username: str) -> bool:
    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import has_flag

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == username))
        user = result.scalar_one_or_none()
        return bool(user and await has_flag(session, user.hostmask, "n"))


def _register_available(loader: Any, name: str, path: Path) -> None:
    # Keep plugin inventory current after creating/uploading files from Web UI.
    loader._available_paths[name] = path  # type: ignore[attr-defined]


def _plugin_source(path_str: str) -> str:
    lower = path_str.replace("\\", "/").lower()
    if "/plugins_extra/" in lower or lower.endswith("/plugins_extra"):
        return "extra"
    return "core"


@router.get("/", response_class=HTMLResponse)
async def plugins_list(
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    loader = bot.plugin_loader
    is_owner = await _is_owner(bot, username)

    if not loader:
        plugins: list[dict[str, Any]] = []
    else:
        from pybot.plugin import get_registry

        registry = get_registry()
        plugins = []
        loaded = loader.get_loaded_plugins()
        for name, path in sorted(loader.get_available_plugins().items()):
            cmds = []
            if name in loaded:
                cmds = [
                    cmd
                    for cmd, handlers in registry.commands.items()
                    if any(h.plugin_name == name for h in handlers)
                ]
            plugins.append(
                {
                    "name": name,
                    "path": str(path),
                    "commands": cmds,
                    "loaded": name in loaded,
                    "meta": _plugin_meta(loader, name),
                    "source": _plugin_source(str(path)),
                }
            )

    return templates.TemplateResponse(
        request,
        "plugins.html",
        {"request": request, "username": username, "plugins": plugins, "is_owner": is_owner},
    )


@router.get("/{plugin_name}", response_class=HTMLResponse)
async def plugin_detail(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    is_owner = await _is_owner(bot, username)
    loader = bot.plugin_loader
    loaded = loader.is_loaded(plugin_name) if loader else False
    available = plugin_name in (loader.get_available_plugins() if loader else {})

    if not available:
        return templates.TemplateResponse(
            request,
            "plugin_detail.html",
            {
                "request": request,
                "username": username,
                "plugin_name": plugin_name,
                "meta": {},
                "commands": [],
                "events": [],
                "intervals": [],
                "rules": [],
                "config_vars": {},
                "loaded": False,
                "available": False,
                "source": "core",
                "is_owner": is_owner,
                "vars_yaml": "{}\n",
                "script_content": "",
                "script_editable": False,
            },
        )

    meta = _plugin_meta(loader, plugin_name)
    available_paths = loader.get_available_plugins() if loader else {}
    plugin_path = str(available_paths.get(plugin_name, ""))
    source = _plugin_source(plugin_path) if plugin_path else "core"
    commands: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    intervals: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []

    if loaded:
        from pybot.plugin import get_registry

        registry = get_registry()
        commands = [
            {
                "command": h.command,
                "aliases": h.aliases,
                "privilege": h.privilege,
                "help": h.help_text,
                "usage": h.usage,
            }
            for handlers in registry.commands.values()
            for h in handlers
            if h.plugin_name == plugin_name
        ]
        events = [
            {"event": event_name, "func": h.func.__name__, "priority": h.priority}
            for event_name, handlers in registry.events.items()
            for h in handlers
            if h.plugin_name == plugin_name
        ]
        intervals = [
            {"seconds": h.seconds, "func": h.func.__name__}
            for h in registry.intervals
            if h.plugin_name == plugin_name
        ]
        rules = [
            {"pattern": h.pattern.pattern, "func": h.func.__name__}
            for h in registry.rules
            if h.plugin_name == plugin_name
        ]

    config_vars: dict[str, Any] = bot.config.plugins.vars.get(plugin_name, {})
    vars_yaml = yaml.safe_dump(config_vars, sort_keys=False, default_flow_style=False)
    script_content = Path(plugin_path).read_text(encoding="utf-8") if plugin_path else ""
    script_editable = source == "extra"

    return templates.TemplateResponse(
        request,
        "plugin_detail.html",
        {
            "request": request,
            "username": username,
            "plugin_name": plugin_name,
            "meta": meta,
            "commands": commands,
            "events": events,
            "intervals": intervals,
            "rules": rules,
            "config_vars": config_vars,
            "loaded": loaded,
            "available": available,
            "source": source,
            "is_owner": is_owner,
            "vars_yaml": vars_yaml,
            "script_content": script_content,
            "script_editable": script_editable,
        },
    )


@router.post("/upload")
async def upload_plugin(
    request: Request,
    plugin_file: Annotated[UploadFile, File(...)],
    username: str = Depends(get_current_user),
    load_now: bool = Form(False),
    overwrite: bool = Form(False),
) -> RedirectResponse:
    bot = request.app.state.bot
    if not await _is_owner(bot, username):
        return RedirectResponse(
            url="/plugins?error=Owner+flag+required+to+upload+plugins.",
            status_code=303,
        )

    name = Path(plugin_file.filename or "").stem.strip().lower()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        return RedirectResponse(
            url="/plugins?error=Invalid+plugin+filename.+Use+letters,+numbers,+and+underscores.",
            status_code=303,
        )
    if not (plugin_file.filename or "").endswith(".py"):
        return RedirectResponse(
            url="/plugins?error=Only+.py+files+can+be+uploaded.",
            status_code=303,
        )

    extra_dir = _resolve_extra_dir(bot)
    extra_dir.mkdir(parents=True, exist_ok=True)
    path = extra_dir / f"{name}.py"
    if path.exists() and not overwrite:
        return RedirectResponse(
            url=f"/plugins?error=Plugin+{name}+already+exists.+Tick+overwrite+to+replace+it.",
            status_code=303,
        )

    content = await plugin_file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return RedirectResponse(
            url="/plugins?error=Plugin+file+must+be+UTF-8+text.",
            status_code=303,
        )

    path.write_text(text, encoding="utf-8")

    if bot.plugin_loader:
        _register_available(bot.plugin_loader, name, path)
        if load_now:
            try:
                await bot.plugin_loader.load(name, path)
            except Exception as exc:
                return RedirectResponse(
                    url=f"/plugins?error=Upload+succeeded+but+load+failed:+{quote_plus(str(exc))}",
                    status_code=303,
                )

    return RedirectResponse(
        url=f"/plugins?success=Uploaded+plugin+{name}+successfully.",
        status_code=303,
    )


@router.post("/new")
async def create_plugin_skeleton(
    request: Request,
    username: str = Depends(get_current_user),
    plugin_name: str = Form(...),
    description: str = Form(""),
    load_now: bool = Form(False),
) -> RedirectResponse:
    bot = request.app.state.bot
    if not await _is_owner(bot, username):
        return RedirectResponse(
            url="/plugins?error=Owner+flag+required+to+create+plugins.",
            status_code=303,
        )

    name = plugin_name.strip().lower()
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
        return RedirectResponse(
            url="/plugins?error=Invalid+plugin+name.+Use+letters,+numbers,+and+underscores.",
            status_code=303,
        )

    extra_dir = _resolve_extra_dir(bot)
    extra_dir.mkdir(parents=True, exist_ok=True)
    path = extra_dir / f"{name}.py"
    if path.exists():
        return RedirectResponse(
            url=f"/plugins?error=Plugin+{name}+already+exists.",
            status_code=303,
        )

    desc = description.strip() or "Describe what this plugin does."
    desc = desc.replace("\n", " ").replace('"', "'")
    today = datetime.now(tz=timezone.utc).date().isoformat()
    class_name = _plugin_class(name)
    skeleton = f'''"""
{class_name} plugin.

Author:  {username}
Version: 0.1.0
Date:    {today}
"""

from __future__ import annotations

__plugin_meta__ = {{
    "author": "{username}",
    "version": "0.1.0",
    "updated": "{today}",
    "description": "{desc}",
    "url": "https://github.com/Jarsky/pyra",
}}

from pybot import plugin
from pybot.plugin import Trigger


@plugin.command(
    "{name}",
    help="{desc}",
    usage="!{name}",
)
async def cmd_{name}(bot: object, trigger: Trigger) -> None:
    await bot.reply(  # type: ignore[attr-defined]
        trigger,
        "{name} plugin loaded. Edit this command in Web UI.",
    )
'''
    path.write_text(skeleton, encoding="utf-8")

    if bot.plugin_loader:
        _register_available(bot.plugin_loader, name, path)
        if load_now:
            try:
                await bot.plugin_loader.load(name, path)
            except Exception as exc:
                return RedirectResponse(
                    url=f"/plugins?error=Skeleton+created+but+load+failed:+{quote_plus(str(exc))}",
                    status_code=303,
                )

    return RedirectResponse(
        url=f"/plugins/{name}?success=Plugin+skeleton+created.",
        status_code=303,
    )


@router.post("/{plugin_name}/save-vars")
async def save_plugin_vars(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
    vars_yaml: str = Form("{}"),
    reload_after: bool = Form(False),
) -> RedirectResponse:
    bot = request.app.state.bot
    if not await _is_owner(bot, username):
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error=Owner+flag+required+to+edit+plugin+vars.",
            status_code=303,
        )

    try:
        parsed = yaml.safe_load(vars_yaml) if vars_yaml.strip() else {}
        if parsed is None:
            parsed = {}
        if not isinstance(parsed, dict):
            raise ValueError("Plugin vars must be a YAML mapping/object.")
    except Exception as exc:
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error={quote_plus(str(exc))}",
            status_code=303,
        )

    config_path = _resolve_config_path()
    try:
        bot.config = save_config_partial(
            config_path,
            bot.config,
            {"plugins": {"vars": {plugin_name: parsed}}},
        )
    except Exception as exc:
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error={quote_plus(str(exc))}",
            status_code=303,
        )

    if reload_after and bot.plugin_loader and bot.plugin_loader.is_loaded(plugin_name):
        try:
            await bot.plugin_loader.reload(plugin_name)
        except Exception as exc:
            return RedirectResponse(
                url=f"/plugins/{plugin_name}?error=Saved+vars+but+reload+failed:+{quote_plus(str(exc))}",
                status_code=303,
            )

    return RedirectResponse(
        url=f"/plugins/{plugin_name}?success=Plugin+vars+saved.",
        status_code=303,
    )


@router.post("/{plugin_name}/save-script")
async def save_plugin_script(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
    script_content: str = Form(...),
    reload_after: bool = Form(False),
) -> RedirectResponse:
    bot = request.app.state.bot
    if not await _is_owner(bot, username):
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error=Owner+flag+required+to+edit+plugin+scripts.",
            status_code=303,
        )

    if not bot.plugin_loader:
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error=Plugin+loader+not+available.",
            status_code=303,
        )

    available = bot.plugin_loader.get_available_plugins()
    path = available.get(plugin_name)
    if not path:
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error=Plugin+not+found.",
            status_code=303,
        )
    if _plugin_source(str(path)) != "extra":
        return RedirectResponse(
            url=f"/plugins/{plugin_name}?error=Script+editing+is+allowed+for+extra+plugins+only.",
            status_code=303,
        )

    path.write_text(script_content, encoding="utf-8")
    _register_available(bot.plugin_loader, plugin_name, path)

    if reload_after and bot.plugin_loader.is_loaded(plugin_name):
        try:
            await bot.plugin_loader.reload(plugin_name)
        except Exception as exc:
            return RedirectResponse(
                url=f"/plugins/{plugin_name}?error=Script+saved+but+reload+failed:+{quote_plus(str(exc))}",
                status_code=303,
            )

    return RedirectResponse(
        url=f"/plugins/{plugin_name}?success=Plugin+script+saved.",
        status_code=303,
    )


@router.post("/{plugin_name}/reload")
async def reload_plugin(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    bot = request.app.state.bot
    if bot.plugin_loader:
        try:
            await bot.plugin_loader.reload(plugin_name)
            return RedirectResponse(
                url=f"/plugins?success={plugin_name}+reloaded+successfully.",
                status_code=303,
            )
        except Exception as exc:
            return RedirectResponse(
                url=f"/plugins?error=Failed+to+reload+{plugin_name}:+{exc}",
                status_code=303,
            )
    return RedirectResponse(url="/plugins", status_code=303)


@router.post("/{plugin_name}/load")
async def load_plugin(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    bot = request.app.state.bot
    if bot.plugin_loader:
        try:
            available = bot.plugin_loader.get_available_plugins()
            if plugin_name not in available:
                return RedirectResponse(
                    url=f"/plugins?error=Plugin+{plugin_name}+not+found.",
                    status_code=303,
                )
            await bot.plugin_loader.load(plugin_name, available[plugin_name])
            return RedirectResponse(
                url=f"/plugins?success={plugin_name}+loaded+successfully.",
                status_code=303,
            )
        except Exception as exc:
            return RedirectResponse(
                url=f"/plugins?error=Failed+to+load+{plugin_name}:+{exc}",
                status_code=303,
            )
    return RedirectResponse(url="/plugins", status_code=303)


@router.post("/{plugin_name}/unload")
async def unload_plugin(
    plugin_name: str,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    bot = request.app.state.bot
    if bot.plugin_loader:
        try:
            await bot.plugin_loader.unload(plugin_name)
            return RedirectResponse(
                url=f"/plugins?success={plugin_name}+unloaded.",
                status_code=303,
            )
        except Exception as exc:
            return RedirectResponse(
                url=f"/plugins?error=Failed+to+unload+{plugin_name}:+{exc}",
                status_code=303,
            )
    return RedirectResponse(url="/plugins", status_code=303)
