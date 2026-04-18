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
                await websocket.send_text(message)

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
    from pybot.core.partyline import execute_partyline_command

    assert isinstance(bot, PyraBot)

    def admin_count() -> int:
        sessions = getattr(bot.partyline, "_sessions", [])
        return len([s for s in sessions if s.authenticated])

    await execute_partyline_command(
        bot=bot,
        actor=username,
        line=line,
        send=lambda msg: queue.put(f">>> {msg.rstrip()}"),
        is_owner=username.lower() == bot.config.core.owner.lower(),
        admin_count=admin_count,
        channel_names=lambda: sorted(ch.name for ch in bot.channels.values()),
        close=websocket.close,
        line_ending="",
    )
