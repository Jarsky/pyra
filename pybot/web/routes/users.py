"""Users management route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pybot.web.app import templates
from pybot.web.auth import get_current_user

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def users_list(
    request: Request,
    username: str = Depends(get_current_user),
    q: str = "",
    page: int = 1,
) -> HTMLResponse:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from pybot.core.database import User, get_session

    limit = 25
    offset = (page - 1) * limit

    async with get_session() as session:
        query = select(User).options(selectinload(User.flags))
        if q:
            query = query.where(User.nick.ilike(f"%{q}%") | User.hostmask.ilike(f"%{q}%"))
        query = query.offset(offset).limit(limit)
        result = await session.execute(query)
        users = result.scalars().all()

    return templates.TemplateResponse(
        "users.html",
        {
            "request": request,
            "username": username,
            "users": users,
            "search": q,
            "page": page,
        },
    )


@router.post("/{user_id}/flags")
async def update_flags(
    user_id: int,
    request: Request,
    username: str = Depends(get_current_user),
    action: str = Form(...),
    flag: str = Form(...),
    channel: str = Form(""),
) -> RedirectResponse:
    from sqlalchemy import select

    from pybot.core.database import User, get_session
    from pybot.core.permissions import add_flag, remove_flag

    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        target_user = result.scalar_one_or_none()
        admin_result = await session.execute(select(User).where(User.nick == username))
        admin_user = admin_result.scalar_one_or_none()

        if not target_user or not admin_user:
            return RedirectResponse(url="/users", status_code=303)

        ch = channel.strip() or None
        try:
            if action == "add":
                await add_flag(session, admin_user.hostmask, target_user.hostmask, flag, channel=ch)
            elif action == "remove":
                await remove_flag(
                    session, admin_user.hostmask, target_user.hostmask, flag, channel=ch
                )
        except PermissionError:
            pass

    return RedirectResponse(url="/users?page=1", status_code=303)


@router.post("/{user_id}/delete")
async def delete_user(
    user_id: int,
    request: Request,
    username: str = Depends(get_current_user),
) -> RedirectResponse:
    from sqlalchemy import select

    from pybot.core.database import User, get_session

    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            await session.delete(user)

    return RedirectResponse(url="/users", status_code=303)
