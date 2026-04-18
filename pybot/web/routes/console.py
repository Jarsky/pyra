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

    assert isinstance(bot, PyraBot)
    lower = line.lower().strip()
    if not lower.startswith("."):
        await queue.put(">>> Unknown command. Use .help")
        return

    cmd, _, _args = lower[1:].partition(" ")
    raw_args = line[1 + len(cmd) :].strip()
    is_owner = username.lower() == bot.config.core.owner.lower()

    if cmd == "help":
        await queue.put(
            ">>> Commands: .help, .who, .channels, .say <#chan> <msg>, .join <#chan>, "
            ".part <#chan>, .reload, .restart (owner), .shutdown (owner), "
            ".raw <line> (owner), .quit"
        )
    elif cmd == "who":
        count = len([s for s in getattr(bot.partyline, "_sessions", []) if s.authenticated])
        await queue.put(f">>> Connected admins: {count}")
    elif cmd == "channels":
        chans = ", ".join(sorted(ch.name for ch in bot.channels.values())) or "(none)"
        await queue.put(f">>> Channels: {chans}")
    elif cmd == "say":
        parts = raw_args.split(None, 1)
        if len(parts) == 2:
            await bot.say(parts[0], parts[1])
            await queue.put(f">>> Sent to {parts[0]}: {parts[1]}")
        else:
            await queue.put(">>> Usage: .say <#chan> <message>")
    elif cmd == "join":
        await bot.join(raw_args)
        await queue.put(f">>> Joining {raw_args}")
    elif cmd == "part":
        await bot.part(raw_args)
        await queue.put(f">>> Parting {raw_args}")
    elif cmd == "reload":
        try:
            await bot.reload_runtime()
            await queue.put(">>> Reloaded config and plugins")
        except Exception as exc:
            await queue.put(f">>> Reload failed: {exc}")
    elif cmd == "raw":
        if not is_owner:
            await queue.put(">>> Permission denied (owner only)")
            return
        await bot.raw(raw_args)
        await queue.put(f">>> Sent raw: {raw_args}")
    elif cmd == "shutdown":
        if not is_owner:
            await queue.put(">>> Permission denied (owner only)")
            return
        await queue.put(">>> Shutting down bot...")
        await bot.shutdown_process("Shutdown from web console")
    elif cmd == "restart":
        if not is_owner:
            await queue.put(">>> Permission denied (owner only)")
            return
        await queue.put(">>> Restarting bot...")
        await bot.restart_process()
    elif cmd == "quit":
        await queue.put(">>> Session closed")
        await websocket.close()
    else:
        await queue.put(">>> Unknown command. Use .help")


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
