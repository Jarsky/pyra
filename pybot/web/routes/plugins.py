"""Plugins management route."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def plugins_list(
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    bot = request.app.state.bot
    loader = bot.plugin_loader

    if not loader:
        plugins: list[dict[str, Any]] = []
    else:
        from pybot.plugin import get_registry

        registry = get_registry()
        plugins = []
        for name, path in sorted(loader.get_loaded_plugins().items()):
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
                    "loaded": True,
                }
            )

    return templates.TemplateResponse(
        request,
        "plugins.html",
        {"request": request, "username": username, "plugins": plugins},
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
        except Exception:  # noqa: S110
            pass
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
        except Exception:  # noqa: S110
            pass
    return RedirectResponse(url="/plugins", status_code=303)
