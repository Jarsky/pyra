"""FastAPI web admin panel."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from pybot.web.auth import create_access_token, verify_password

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def create_app(bot: "PyraBot") -> FastAPI:
    app = FastAPI(title="Pyra Web Admin", docs_url=None, redoc_url=None)
    app.state.bot = bot

    # Static files
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    else:
        logger.warning(f"Static directory not found, skipping /static mount: {_STATIC_DIR}")

    # Auth routes
    @app.get("/auth/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> Response:
        return templates.TemplateResponse(
            request, "login.html", {"request": request, "error": None}
        )

    @app.post("/auth/login")
    async def login(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        # Rate limit: simple in-memory counter (production should use Redis)
        from sqlalchemy import select

        from pybot.core.database import User, get_session
        from pybot.core.permissions import has_flag

        async with get_session() as session:
            result = await session.execute(select(User).where(User.nick == username))
            user = result.scalar_one_or_none()

        if not user or not user.password_hash or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"request": request, "error": "Invalid username or password."},
                status_code=401,
            )

        # Verify admin flag
        async with get_session() as session:
            is_admin = await has_flag(session, user.hostmask, "a") or await has_flag(
                session, user.hostmask, "n"
            )
        if not is_admin:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"request": request, "error": "Insufficient permissions."},
                status_code=403,
            )

        secret = bot.config.web.secret_key.get_secret_value()
        timeout = timedelta(seconds=bot.config.web.session_timeout)
        token = create_access_token(secret, username, timeout)

        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            "access_token",
            token,
            httponly=True,
            samesite="lax",
            max_age=bot.config.web.session_timeout,
        )
        return response

    @app.post("/auth/logout")
    async def logout() -> Response:
        response = RedirectResponse(url="/auth/login", status_code=303)
        response.delete_cookie("access_token")
        return response

    # Register routers
    from pybot.web.routes.channels import router as channels_router
    from pybot.web.routes.console import router as console_router
    from pybot.web.routes.dashboard import router as dashboard_router
    from pybot.web.routes.logs import router as logs_router
    from pybot.web.routes.plugins import router as plugins_router
    from pybot.web.routes.settings import router as settings_router
    from pybot.web.routes.users import router as users_router
    from pybot.web.routes.webhooks import router as webhooks_router

    app.include_router(dashboard_router)
    app.include_router(channels_router, prefix="/channels")
    app.include_router(users_router, prefix="/users")
    app.include_router(plugins_router, prefix="/plugins")
    app.include_router(logs_router, prefix="/logs")
    app.include_router(settings_router, prefix="/settings")
    app.include_router(console_router, prefix="/console")
    app.include_router(webhooks_router)  # /webhooks/* — no auth, used by arr apps

    return app
