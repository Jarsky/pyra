from __future__ import annotations

import asyncio

import pytest

from pybot.core.services import ServiceCommandResult, ServicesInterface


class _DummyIRC:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def privmsg(self, target: str, text: str) -> None:
        self.sent.append((target, text))


class _DummyBot:
    def __init__(self) -> None:
        self.irc = _DummyIRC()


@pytest.mark.asyncio
async def test_memoserv_send_checked_success() -> None:
    bot = _DummyBot()
    services = ServicesInterface(bot)  # type: ignore[arg-type]

    task = asyncio.create_task(services.memoserv_send_checked("alice", "hello"))
    await asyncio.sleep(0)

    assert bot.irc.sent == [("MemoServ", "SEND alice hello")]
    services.on_notice("MemoServ", "Memo has been sent.")

    result = await task
    assert isinstance(result, ServiceCommandResult)
    assert result.ok
    assert not result.timed_out
    assert result.message == "Memo has been sent."


@pytest.mark.asyncio
async def test_chanserv_akick_add_checked_error() -> None:
    bot = _DummyBot()
    services = ServicesInterface(bot)  # type: ignore[arg-type]

    task = asyncio.create_task(services.chanserv_akick_add_checked("#test", "bad!*@*"))
    await asyncio.sleep(0)

    assert bot.irc.sent == [("ChanServ", "AKICK #test ADD bad!*@*")]
    services.on_notice("ChanServ", "Error: You are not authorized to perform this operation.")

    result = await task
    assert not result.ok
    assert not result.timed_out
    assert "not authorized" in result.message.lower()


@pytest.mark.asyncio
async def test_request_timeout_returns_structured_failure() -> None:
    bot = _DummyBot()
    services = ServicesInterface(bot)  # type: ignore[arg-type]

    result = await services._request_with_notice_result(
        "ChanServ",
        "AKICK #test LIST",
        timeout=0.01,
    )

    assert not result.ok
    assert result.timed_out
    assert "No response from ChanServ" in result.message


@pytest.mark.asyncio
async def test_nickserv_status_still_resolves() -> None:
    bot = _DummyBot()
    services = ServicesInterface(bot)  # type: ignore[arg-type]

    task = asyncio.create_task(services.nickserv_status("alice"))
    await asyncio.sleep(0)

    assert bot.irc.sent == [("NickServ", "STATUS alice")]
    services.on_notice("NickServ", "STATUS alice 3")
    level = await task

    assert level == 3
