"""Web interface authentication — JWT + bcrypt."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from passlib.context import CryptContext

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot

_crypt_ctx = CryptContext(schemes=["bcrypt"])
ALGORITHM = "HS256"


def create_access_token(secret_key: str, username: str, expires_delta: timedelta) -> str:
    expire = datetime.now(tz=timezone.utc) + expires_delta
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, secret_key, algorithm=ALGORITHM)


def verify_password(plain: str, hashed: str) -> bool:
    return _crypt_ctx.verify(plain, hashed)


def hash_password(password: str) -> str:
    return _crypt_ctx.hash(password)


def decode_token(token: str, secret_key: str) -> str | None:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


async def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
) -> str:
    """FastAPI dependency — returns username or redirects to login."""
    bot: "PyraBot" = request.app.state.bot
    secret = bot.config.web.secret_key.get_secret_value()

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )

    username = decode_token(access_token, secret)
    if not username:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )
    return username


async def require_admin(
    username: str = Depends(get_current_user),
    request: Request = None,
) -> str:
    """Alias for get_current_user — all web users must have a or n flag."""
    return username
