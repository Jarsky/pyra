"""Web interface authentication — JWT + bcrypt."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import bcrypt
import jwt
from fastapi import Cookie, Depends, HTTPException, Request, status

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
    except jwt.PyJWTError:
        return None


async def get_web_user_flags(username: str) -> set[str]:
    """Flags of the web user, or an empty set if the user no longer exists."""
    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import get_user_flags

    async with get_session() as session:
        result = await session.execute(select(User).where(User.nick == username))
        user = result.scalars().first()
        if user is None or not user.password_hash:
            return set()
        return await get_user_flags(session, user)


async def authenticate_token(bot: "PyraBot", token: str | None) -> tuple[str, set[str]] | None:
    """Validate a session token: returns (username, flags) for a current admin, else None.

    Flags are re-read on every request, so removing a user's a/n flag (or deleting the
    user) ends their web session immediately rather than when the token expires.
    """
    if not token:
        return None
    username = decode_token(token, bot.config.web.secret_key.get_secret_value())
    if not username:
        return None
    flags = await get_web_user_flags(username)
    if "n" not in flags and "a" not in flags:
        return None
    return username, flags


async def get_current_user(
    request: Request,
    access_token: str | None = Cookie(default=None),
) -> str:
    """FastAPI dependency — returns username or redirects to login."""
    bot: "PyraBot" = request.app.state.bot
    session = await authenticate_token(bot, access_token)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/auth/login"},
        )
    request.state.user_flags = session[1]
    return session[0]


def is_owner_request(request: Request) -> bool:
    """True if the user authenticated by get_current_user holds the owner flag."""
    return "n" in getattr(request.state, "user_flags", set())


async def require_admin(
    username: str = Depends(get_current_user),
    request: Request | None = None,
) -> str:
    """Alias for get_current_user, which already requires the a or n flag."""
    return username
