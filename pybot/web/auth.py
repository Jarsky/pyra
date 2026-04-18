"""Web interface authentication — JWT + bcrypt."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt

if TYPE_CHECKING:
    from pybot.core.bot import PyraBot

ALGORITHM = "HS256"
_PW_SCHEME_PREFIX = "pyra_sha256_bcrypt$"


def create_access_token(secret_key: str, username: str, expires_delta: timedelta) -> str:
    expire = datetime.now(tz=timezone.utc) + expires_delta
    payload = {"sub": username, "exp": expire}
    return cast(str, jwt.encode(payload, secret_key, algorithm=ALGORITHM))


def verify_password(plain: str, hashed: str) -> bool:
    try:
        if hashed.startswith(_PW_SCHEME_PREFIX):
            stored_hash = hashed[len(_PW_SCHEME_PREFIX) :].encode("utf-8")
            digest = hashlib.sha256(plain.encode("utf-8")).hexdigest().encode("utf-8")
            return cast(bool, bcrypt.checkpw(digest, stored_hash))

        # Backward compatibility for existing plain bcrypt hashes.
        return cast(bool, bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8")))
    except ValueError:
        # Raised when checking long passwords against legacy bcrypt hashes.
        return False


def hash_password(password: str) -> str:
    digest = hashlib.sha256(password.encode("utf-8")).hexdigest().encode("utf-8")
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt()).decode("utf-8")
    return f"{_PW_SCHEME_PREFIX}{hashed}"


def decode_token(token: str, secret_key: str) -> str | None:
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return cast(str | None, payload.get("sub"))
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
    request: Request | None = None,
) -> str:
    """Alias for get_current_user — all web users must have a or n flag."""
    return username
