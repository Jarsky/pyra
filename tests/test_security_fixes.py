"""Regression tests for security and correctness fixes (identity, redaction, IRC output, SASL)."""

from __future__ import annotations

import asyncio
import base64
from datetime import timedelta
from typing import Any

import pytest

from pybot.core import irc as irc_module
from pybot.core.bot import PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import Ignore, User, close_db, get_session, init_db
from pybot.core.irc import (
    MAX_LINE_BYTES,
    PREFIX_RESERVE_BYTES,
    IRCConnection,
    IRCMessage,
    clean_line,
    split_message,
)
from pybot.core.permissions import add_flag, add_owner_bootstrap, has_flag
from pybot.core.redact import redact_line, redact_message
from pybot.core.sasl import ScramError, ScramSha256
from pybot.plugin import get_registry

parse = IRCMessage.parse


@pytest.fixture
async def db() -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    yield
    await close_db()


def _bot(config_dict: dict, **core: Any) -> PyraBot:
    cfg = dict(config_dict)
    cfg["core"] = {**config_dict["core"], **core}
    return PyraBot(BotConfig.model_validate(cfg))


def _queued(conn: IRCConnection) -> list[str]:
    lines = []
    while not conn._flood_queue.empty():
        lines.append(conn._flood_queue.get_nowait())
    return lines


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("target", "text", "expected"),
    [
        ("NickServ", "IDENTIFY hunter2", "IDENTIFY [REDACTED]"),
        ("Q@CServe.quakenet.org", "AUTH me hunter2", "AUTH [REDACTED]"),
        ("Pyra", "!passwd hunter2hunter2", "!passwd [REDACTED]"),
        ("#chan", "!setpass bob hunter2hunter2", "!setpass [REDACTED]"),
        ("#chan", "!help passwd", "!help passwd"),
        ("#chan", "identify yourself", "identify yourself"),
    ],
)
def test_redact_message(target: str, text: str, expected: str) -> None:
    assert redact_message(target, text, "!") == expected


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("PRIVMSG NickServ :IDENTIFY pw", "PRIVMSG NickServ :IDENTIFY [REDACTED]"),
        (
            "@time=x :bob!u@h PRIVMSG Pyra :!passwd secretpass",
            "@time=x :bob!u@h PRIVMSG Pyra :!passwd [REDACTED]",
        ),
        ("PASS :serverpass", "PASS [REDACTED]"),
        ("AUTHENTICATE AGJvYgBwYXNz", "AUTHENTICATE [REDACTED]"),
        ("AUTHENTICATE PLAIN", "AUTHENTICATE PLAIN"),
        ("PRIVMSG #chan :hello :) there", "PRIVMSG #chan :hello :) there"),
    ],
)
def test_redact_line(line: str, expected: str) -> None:
    assert redact_line(line, "!") == expected


def test_log_and_partyline_text_are_redacted(minimal_config_dict: dict) -> None:
    from pybot.core.partyline import _format_irc_event

    bot = _bot(minimal_config_dict)
    msg = parse(":bob!u@h PRIVMSG TestBot :!passwd supersecret1")
    assert bot._sanitize_log_message(msg) == "!passwd [REDACTED]"
    line = _format_irc_event(msg, text=bot._sanitize_log_message(msg))
    assert line is not None and "supersecret1" not in line


# ---------------------------------------------------------------------------
# Outgoing IRC text
# ---------------------------------------------------------------------------


def test_clean_line_removes_line_breaks() -> None:
    assert clean_line("hi\r\nQUIT :bye\0") == "hi  QUIT :bye"


async def test_privmsg_cannot_inject_commands(minimal_config_dict: dict) -> None:
    conn = IRCConnection(BotConfig.model_validate(minimal_config_dict), lambda _m: None)
    await conn.privmsg("#chan", "title\r\nQUIT :pwned")
    await conn.kick("#chan", "bob", "bye\r\nPRIVMSG NickServ :DROP")
    lines = _queued(conn)
    assert lines == [
        "PRIVMSG #chan :title",
        "PRIVMSG #chan :QUIT :pwned",
        "KICK #chan bob :bye  PRIVMSG NickServ :DROP",
    ]
    assert all("\r" not in line and "\n" not in line for line in lines)


async def test_long_messages_are_split_within_line_limit(minimal_config_dict: dict) -> None:
    conn = IRCConnection(BotConfig.model_validate(minimal_config_dict), lambda _m: None)
    text = " ".join(f"wörd{i}" for i in range(300))  # multi-byte characters too
    await conn.privmsg("#chan", text)
    lines = _queued(conn)
    assert len(lines) > 1
    for line in lines:
        assert len(line.encode()) + 2 + PREFIX_RESERVE_BYTES <= MAX_LINE_BYTES
    rejoined = " ".join(line.split(" :", 1)[1] for line in lines)
    assert rejoined == text


def test_ctcp_is_truncated_not_split() -> None:
    parts = split_message("\x01ACTION " + "x" * 1000 + "\x01", 100)
    assert len(parts) == 1 and parts[0].startswith("\x01ACTION") and parts[0].endswith("\x01")
    assert len(parts[0].encode()) <= 100


# ---------------------------------------------------------------------------
# SASL SCRAM-SHA-256 (RFC 7677 test vector)
# ---------------------------------------------------------------------------

RFC_NONCE = "rOprNGfwEbeRWgbNEkqO"
RFC_SERVER_FIRST = (
    "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096"
)
RFC_CLIENT_FINAL = (
    "c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
    "p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
)
RFC_SERVER_FINAL = "v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_scram_matches_rfc7677_vector() -> None:
    scram = ScramSha256("user", "pencil", nonce=RFC_NONCE)
    assert scram.client_first() == f"n,,n=user,r={RFC_NONCE}"
    assert scram.client_final(RFC_SERVER_FIRST) == RFC_CLIENT_FINAL
    scram.verify_server_final(RFC_SERVER_FINAL)


def test_scram_rejects_bad_server() -> None:
    scram = ScramSha256("user", "pencil", nonce=RFC_NONCE)
    with pytest.raises(ScramError, match="nonce"):
        scram.client_final("r=someoneelse,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
    scram.client_final(RFC_SERVER_FIRST)
    with pytest.raises(ScramError, match="signature"):
        scram.verify_server_final("v=" + base64.b64encode(b"x" * 32).decode())


async def test_scram_exchange_over_irc(minimal_config_dict: dict, monkeypatch: Any) -> None:
    cfg = dict(minimal_config_dict)
    cfg["auth"] = {
        **minimal_config_dict["auth"],
        "auth_method": "sasl",
        "sasl_mechanism": "SCRAM-SHA-256",
        "sasl_username": "user",
        "sasl_password": "pencil",
    }
    conn = IRCConnection(BotConfig.model_validate(cfg), lambda _m: None)
    sent: list[str] = []

    async def fake_send_raw(line: str) -> None:
        sent.append(line)

    conn.send_raw = fake_send_raw  # type: ignore[method-assign]
    real = ScramSha256
    monkeypatch.setattr(irc_module, "ScramSha256", lambda u, p: real(u, p, nonce=RFC_NONCE))

    def b64(s: str) -> str:
        return base64.b64encode(s.encode()).decode()

    await conn._on_authenticate(parse("AUTHENTICATE +"))
    await conn._on_authenticate(parse(f"AUTHENTICATE {b64(RFC_SERVER_FIRST)}"))
    await conn._on_authenticate(parse(f"AUTHENTICATE {b64(RFC_SERVER_FINAL)}"))

    assert sent == [
        f"AUTHENTICATE {b64(f'n,,n=user,r={RFC_NONCE}')}",
        f"AUTHENTICATE {b64(RFC_CLIENT_FINAL)}",
        "AUTHENTICATE +",
    ]


# ---------------------------------------------------------------------------
# Dispatch: ignores, channel restrictions
# ---------------------------------------------------------------------------


async def _run_tasks() -> None:
    """Let handler tasks spawned by dispatch finish (before the db fixture closes)."""
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if pending:
        await asyncio.wait(pending, timeout=5)


async def test_ignored_users_cannot_trigger_commands(db: None, minimal_config_dict: dict) -> None:
    bot = _bot(minimal_config_dict)
    calls: list[str] = []

    async def handler(_bot: Any, trigger: Any) -> None:
        calls.append(trigger.nick)

    from pybot.plugin import CommandHandler

    registry = get_registry()
    registry.commands.setdefault("sectest", []).append(
        CommandHandler("sectest", handler, "sectest")
    )
    try:
        async with get_session() as session:
            session.add(Ignore(hostmask="*!*@spam.example", set_by="test", active=True))

        await bot._dispatch_to_plugins(parse(":spammer!u@spam.example PRIVMSG #test :!sectest"))
        await bot._dispatch_to_plugins(parse(":friend!u@good.example PRIVMSG #test :!sectest"))
        await _run_tasks()
        assert calls == ["friend"]
    finally:
        registry.clear_plugin("sectest")


async def test_command_channel_restriction(db: None, minimal_config_dict: dict) -> None:
    bot = _bot(minimal_config_dict)
    calls: list[str] = []

    async def handler(_bot: Any, trigger: Any) -> None:
        calls.append(trigger.channel)

    from pybot.plugin import CommandHandler

    registry = get_registry()
    registry.commands.setdefault("chantest", []).append(
        CommandHandler("chantest", handler, "chantest", channels=["#Allowed"])
    )
    try:
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #other :!chantest"))
        await bot._dispatch_to_plugins(parse(":a!u@h PRIVMSG #allowed :!chantest"))
        await _run_tasks()
        assert calls == ["#allowed"]
    finally:
        registry.clear_plugin("chantest")


# ---------------------------------------------------------------------------
# Owner identity
# ---------------------------------------------------------------------------


async def test_bootstrap_clears_legacy_nick_only_owner_mask(
    db: None, minimal_config_dict: dict
) -> None:
    async with get_session() as session:
        await add_owner_bootstrap(session, "TestOwner", "TestOwner!*@*")  # what v1.0 created

    bot = _bot(minimal_config_dict)
    await bot._bootstrap_owner_account()

    trigger = await bot._build_trigger(
        parse(":TestOwner!evil@attacker PRIVMSG #test :hi"), [], None
    )
    assert not trigger.owner and not trigger.admin


async def test_bootstrap_uses_configured_owner_identity(
    db: None, minimal_config_dict: dict
) -> None:
    bot = _bot(minimal_config_dict, owner_hostmask="*!real@home.example", owner_account="ownacct")
    await bot._bootstrap_owner_account()

    async with get_session() as session:
        assert await has_flag(session, "AnyNick!real@home.example", "n")
        assert await has_flag(session, "AnyNick!x@elsewhere", "n", account="OwnAcct")
        assert not await has_flag(session, "TestOwner!evil@attacker", "n")


async def test_bootstrap_ignores_nick_only_owner_hostmask(
    db: None, minimal_config_dict: dict
) -> None:
    bot = _bot(minimal_config_dict, owner_hostmask="TestOwner!*@*")
    await bot._bootstrap_owner_account()
    async with get_session() as session:
        assert not await has_flag(session, "TestOwner!evil@attacker", "n")


# ---------------------------------------------------------------------------
# Partyline and web logins require admin flags
# ---------------------------------------------------------------------------


async def test_partyline_refuses_users_without_admin_flag(
    db: None, minimal_config_dict: dict
) -> None:
    from pybot.core.partyline import PartylineSession
    from pybot.web.auth import hash_password

    async with get_session() as session:
        session.add(
            User(nick="voiceuser", hostmask="v!u@h", password_hash=hash_password("pw12345678"))
        )
        session.add(
            User(nick="adminuser", hostmask="a!u@h", password_hash=hash_password("pw12345678"))
        )
    async with get_session() as session:
        await add_flag(session, {"n"}, "a!u@h", "a")

    bot = _bot(minimal_config_dict)
    session_obj = PartylineSession(None, None, bot, None)  # type: ignore[arg-type]
    assert not await session_obj._verify_credentials("voiceuser", "pw12345678")
    assert await session_obj._verify_credentials("adminuser", "pw12345678")
    assert session_obj.owner is False
    assert await session_obj._verify_credentials("TestOwner", "testpass")  # config owner password
    assert session_obj.owner is True


async def test_web_session_ends_when_admin_flag_removed(
    db: None, minimal_config_dict: dict
) -> None:
    from pybot.core.permissions import remove_flag
    from pybot.web.auth import authenticate_token, create_access_token, hash_password

    async with get_session() as session:
        session.add(User(nick="adm", hostmask="adm!u@h", password_hash=hash_password("pw12345678")))
    async with get_session() as session:
        await add_flag(session, {"n"}, "adm!u@h", "a")

    bot = _bot(minimal_config_dict)
    token = create_access_token(
        "testsecret-0123456789abcdef0123456789abcdef", "adm", timedelta(minutes=5)
    )
    assert await authenticate_token(bot, token) == ("adm", {"a"})

    async with get_session() as session:
        await remove_flag(session, {"n"}, "adm!u@h", "a")
    assert await authenticate_token(bot, token) is None


# ---------------------------------------------------------------------------
# Connection behaviour
# ---------------------------------------------------------------------------


async def test_failed_server_rotates_to_next(minimal_config_dict: dict, monkeypatch: Any) -> None:
    cfg = dict(minimal_config_dict)
    cfg["servers"] = [
        {**minimal_config_dict["servers"][0], "host": "second.example", "priority": 2},
        {**minimal_config_dict["servers"][0], "host": "first.example", "priority": 1},
    ]
    conn = IRCConnection(BotConfig.model_validate(cfg), lambda _m: None)
    tried: list[str] = []

    async def fake_connect() -> None:
        tried.append(conn.current_server.host)
        if len(tried) == 1:
            raise ConnectionRefusedError()
        conn._running = False

    async def no_sleep(_s: float) -> None:
        return None

    conn._connect_and_run = fake_connect  # type: ignore[method-assign]
    monkeypatch.setattr(irc_module.asyncio, "sleep", no_sleep)
    await conn.run()
    assert tried == ["first.example", "second.example"]


async def test_rejoin_on_kick_can_be_disabled(minimal_config_dict: dict) -> None:
    cfg = dict(minimal_config_dict)
    cfg["channels"] = {"autojoin": [], "rejoin_on_kick": False}
    bot = PyraBot(BotConfig.model_validate(cfg))
    await bot._handle_kick(parse(":op!u@h KICK #test TestBot :bye"))
    assert _queued(bot.irc) == []


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------


def test_setup_auth_choices_produce_valid_config(minimal_config_dict: dict) -> None:
    import copy

    from pybot.scripts.setup import apply_auth_choice, owner_mask_problem

    cfg = copy.deepcopy(minimal_config_dict)
    apply_auth_choice(cfg, "sasl_scram", "TestBot", "secret")
    auth = BotConfig.model_validate(cfg).auth
    assert (auth.auth_method, auth.sasl_mechanism, auth.sasl_username) == (
        "sasl",
        "SCRAM-SHA-256",
        "TestBot",
    )
    assert owner_mask_problem("*!*@*") is not None
    assert owner_mask_problem("Me!*@*") is not None
    assert owner_mask_problem("*!me@home.example") is None
    assert owner_mask_problem("") is None
