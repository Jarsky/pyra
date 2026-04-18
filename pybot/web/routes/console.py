"""Console route — browser-based partyline via WebSocket."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

from pybot.web.app import templates
from pybot.web.auth import decode_token, get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def console_view(
    request: Request,
    username: str = Depends(get_current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "console.html",
        {"request": request, "username": username},
    )


@router.websocket("/ws")
async def console_ws(websocket: WebSocket) -> None:
    """WebSocket endpoint — streams IRC events and accepts commands."""
    bot = websocket.app.state.bot

    # Authenticate via cookie
    token = websocket.cookies.get("access_token")
    if not token:
        await websocket.close(code=4001)
        return

    secret = bot.config.web.secret_key.get_secret_value()
    username = decode_token(token, secret)
    if not username:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    # Subscribe to IRC event broadcasts
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)

    async def on_event(message: str) -> None:
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass

    # Attach to partyline broadcast if available
    if bot.partyline:
        bot.partyline._ws_queues = getattr(bot.partyline, "_ws_queues", [])
        bot.partyline._ws_queues.append(queue)

    try:

        async def sender() -> None:
            while True:
                message = await queue.get()
                html = f'<div class="log-line">{_escape(message)}</div>\n'
                await websocket.send_text(html)

        async def receiver() -> None:
            async for data in websocket.iter_text():
                # Command from browser — execute as partyline command
                line = data.strip()
                if not line:
                    continue
                # Echo back
                await queue.put(f"[{username}] {line}")
                # Parse and execute basic commands
                await _handle_ws_command(bot, websocket, username, line, queue)

        await asyncio.gather(sender(), receiver())

    except WebSocketDisconnect:
        pass
    finally:
        if bot.partyline and hasattr(bot.partyline, "_ws_queues"):
            try:
                bot.partyline._ws_queues.remove(queue)
            except ValueError:
                pass


async def _handle_ws_command(
    bot: object,
    websocket: WebSocket,
    username: str,
    line: str,
    queue: asyncio.Queue,
) -> None:
    from pybot.core.bot import PyraBot

    assert isinstance(bot, PyraBot)
    lower = line.lower()

    if lower.startswith("!say "):
        parts = line[5:].split(None, 1)
        if len(parts) == 2:
            await bot.say(parts[0], parts[1])
            await queue.put(f">>> Sent to {parts[0]}: {parts[1]}")

    elif lower.startswith("!join "):
        channel = line[6:].strip()
        await bot.join(channel)

    elif lower.startswith("!part "):
        channel = line[6:].strip()
        await bot.part(channel)

    elif lower.startswith("!reload ") and bot.plugin_loader:
        name = line[8:].strip()
        try:
            await bot.plugin_loader.reload(name)
            await queue.put(f">>> Plugin '{name}' reloaded.")
        except Exception as exc:
            await queue.put(f">>> Error: {exc}")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
