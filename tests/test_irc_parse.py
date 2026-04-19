"""Tests for IRCMessage.parse()."""

from __future__ import annotations

import asyncio

import pytest

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.irc import IRCConnection, IRCMessage


def parse(line: str) -> IRCMessage:
    return IRCMessage.parse(line)


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def test_simple_privmsg() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :Hello world")
    assert msg.command == "PRIVMSG"
    assert msg.nick == "nick"
    assert msg.user == "user"
    assert msg.host == "host"
    assert msg.channel == "#channel"
    assert msg.text == "Hello world"
    assert msg.hostmask == "nick!user@host"


def test_numeric_reply() -> None:
    msg = parse(":irc.example.com 001 MyBot :Welcome to the IRC network MyBot")
    assert msg.command == "001"
    assert msg.prefix == "irc.example.com"
    assert msg.nick == "irc.example.com"  # no ! in prefix
    assert msg.params[0] == "MyBot"
    assert msg.text == "Welcome to the IRC network MyBot"


def test_join_no_text() -> None:
    msg = parse(":nick!user@host JOIN #channel")
    assert msg.command == "JOIN"
    assert msg.nick == "nick"
    assert msg.params == ["#channel"]


def test_extended_join() -> None:
    msg = parse(":nick!user@host JOIN #channel accountname :Real Name")
    assert msg.command == "JOIN"
    assert msg.params[0] == "#channel"
    assert msg.params[1] == "accountname"


def test_ping_no_prefix() -> None:
    msg = parse("PING :irc.example.com")
    assert msg.command == "PING"
    assert msg.prefix is None
    assert msg.text == "irc.example.com"


def test_quit_with_reason() -> None:
    msg = parse(":nick!user@host QUIT :Goodbye cruel world")
    assert msg.command == "QUIT"
    assert msg.text == "Goodbye cruel world"


def test_nick_change() -> None:
    msg = parse(":oldnick!user@host NICK :newnick")
    assert msg.command == "NICK"
    assert msg.nick == "oldnick"
    assert msg.text == "newnick"


def test_mode_channel() -> None:
    msg = parse(":op!user@host MODE #channel +o someone")
    assert msg.command == "MODE"
    assert msg.params == ["#channel", "+o", "someone"]


def test_kick() -> None:
    msg = parse(":op!user@host KICK #channel victim :Spamming")
    assert msg.command == "KICK"
    assert msg.params[0] == "#channel"
    assert msg.params[1] == "victim"
    assert msg.text == "Spamming"


def test_topic_change() -> None:
    msg = parse(":nick!user@host TOPIC #channel :New topic here")
    assert msg.command == "TOPIC"
    assert msg.channel == "#channel"
    assert msg.text == "New topic here"


def test_names_reply() -> None:
    msg = parse(":server 353 bot = #channel :@op +voice regular")
    assert msg.command == "353"
    assert msg.params[-1] == "@op +voice regular"


def test_privmsg_pm() -> None:
    """PRIVMSG directed to bot (PM), not a channel."""
    msg = parse(":nick!user@host PRIVMSG MyBot :hello there")
    assert msg.command == "PRIVMSG"
    assert msg.params[0] == "MyBot"
    assert not (msg.channel)  # no channel


def test_notice() -> None:
    msg = parse(":server NOTICE * :This is a notice")
    assert msg.command == "NOTICE"
    assert msg.text == "This is a notice"


def test_part_with_message() -> None:
    msg = parse(":nick!user@host PART #channel :Leaving")
    assert msg.command == "PART"
    assert msg.text == "Leaving"


# ---------------------------------------------------------------------------
# CTCP
# ---------------------------------------------------------------------------


def test_ctcp_version() -> None:
    msg = parse(":nick!user@host PRIVMSG bot :\x01VERSION\x01")
    assert msg.ctcp_command == "VERSION"


def test_ctcp_action() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :\x01ACTION waves\x01")
    assert msg.ctcp_command == "ACTION"
    assert msg.ctcp_text == "waves"


def test_malformed_ctcp_missing_trailing_delimiter_is_ignored() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :\x01PING 12345")
    assert msg.ctcp_command is None
    assert msg.ctcp_text == ""


def test_malformed_ctcp_nested_delimiter_is_ignored() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :\x01PING abc\x01def\x01")
    assert msg.ctcp_command is None
    assert msg.ctcp_text == ""


def test_ctcp_tab_separator_parses_command_and_payload() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :\x01PING\t12345\x01")
    assert msg.ctcp_command == "PING"
    assert msg.ctcp_text == "12345"


def test_ctcp_whitespace_only_body_is_ignored() -> None:
    msg = parse(":nick!user@host PRIVMSG #channel :\x01   \t  \x01")
    assert msg.ctcp_command is None
    assert msg.ctcp_text == ""


# ---------------------------------------------------------------------------
# IRCv3 message tags
# ---------------------------------------------------------------------------


def test_message_tags_basic() -> None:
    msg = parse("@time=2023-01-01T12:00:00Z :nick!user@host PRIVMSG #ch :hi")
    assert msg.tags.get("time") == "2023-01-01T12:00:00Z"
    assert msg.command == "PRIVMSG"
    assert msg.nick == "nick"


def test_message_tags_account() -> None:
    msg = parse("@account=myaccount :nick!user@host PRIVMSG #ch :hello")
    assert msg.account_tag == "myaccount"


def test_message_tags_multiple() -> None:
    msg = parse("@time=2023;account=acc;+custom=val :n!u@h PRIVMSG #ch :test")
    assert msg.tags["time"] == "2023"
    assert msg.tags["account"] == "acc"
    assert msg.tags["+custom"] == "val"


def test_tag_value_escaping() -> None:
    msg = parse(r"@key=hello\:world :n!u@h PRIVMSG #ch :x")
    assert msg.tags["key"] == "hello;world"


def test_cap_ls() -> None:
    msg = parse(":server CAP * LS :sasl multi-prefix extended-join")
    assert msg.command == "CAP"
    assert msg.params[1] == "LS"
    assert "sasl" in msg.params[-1]


def test_authenticate() -> None:
    msg = parse("AUTHENTICATE +")
    assert msg.command == "AUTHENTICATE"
    assert msg.params == ["+"]


def test_sasl_900() -> None:
    msg = parse(":server 900 bot nick!user@host accountname :You are now logged in as accountname")
    assert msg.command == "900"
    assert msg.params[2] == "accountname"


def test_empty_params() -> None:
    msg = parse(":server 004 bot irc.example.com")
    assert msg.command == "004"
    assert msg.params == ["bot", "irc.example.com"]


@pytest.mark.asyncio
async def test_isupport_prefix_chanmodes_network_parsed(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    msg = parse(
        ":irc.example.com 005 TestBot "
        "PREFIX=(qaohv)~&@%+ "
        "CHANMODES=IXbeg,k,Hfjl,ACKMORTcimnprstz "
        "NETWORK=Elements :are supported by this server"
    )
    await conn._on_005(msg)

    assert conn.network_name == "Elements"
    assert conn.chanmodes == "IXbeg,k,Hfjl,ACKMORTcimnprstz"
    assert conn.mode_to_prefix["o"] == "@"
    assert conn.prefix_to_mode["%"] == "h"
    assert conn.nick_prefix_chars == "~&@%+"
    assert conn.nick_prefix_modes == {"q", "a", "o", "h", "v"}


@pytest.mark.asyncio
async def test_names_uses_dynamic_prefix_chars(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    channel = "#test"
    bot.channels[channel.lower()] = ChannelState(name=channel)
    bot.irc.nick_prefix_chars = "~&@%+"

    names = parse(":server 353 TestBot = #test :~owner &admin @op %half +voice plain")
    end = parse(":server 366 TestBot #test :End of /NAMES list.")
    await bot._handle_names(names)
    await bot._handle_end_of_names(end)

    ch = bot.channels[channel.lower()]
    assert ch.get_nick("owner") is not None
    assert ch.get_nick("admin") is not None
    assert ch.get_nick("op") is not None
    assert ch.get_nick("half") is not None
    assert ch.get_nick("voice") is not None
    assert ch.get_nick("plain") is not None


@pytest.mark.asyncio
async def test_mode_uses_dynamic_prefix_modes(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    channel = "#test"
    bot.channels[channel.lower()] = ChannelState(name=channel)
    bot.channels[channel.lower()].add_nick("alice")
    bot.irc.nick_prefix_modes = {"q", "a", "o", "h", "v"}

    await bot._handle_mode(parse(":op!u@h MODE #test +h alice"))
    ns = bot.channels[channel.lower()].get_nick("alice")
    assert ns is not None
    assert "h" in ns.modes

    await bot._handle_mode(parse(":op!u@h MODE #test -h alice"))
    ns = bot.channels[channel.lower()].get_nick("alice")
    assert ns is not None
    assert "h" not in ns.modes


@pytest.mark.asyncio
async def test_mode_parser_consumes_non_prefix_args_before_prefix_modes(
    minimal_config_dict: dict,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    channel = "#test"
    bot.channels[channel.lower()] = ChannelState(name=channel)
    bot.channels[channel.lower()].add_nick("alice")
    bot.irc.nick_prefix_modes = {"o", "v"}
    bot.irc.chanmodes_a = {"b", "e", "I"}

    await bot._handle_mode(parse(":op!u@h MODE #test +bo bad!*@* alice"))

    ch = bot.channels[channel.lower()]
    ns = ch.get_nick("alice")
    assert ns is not None
    assert "o" in ns.modes
    assert "bad!*@*" in ch.bans


@pytest.mark.asyncio
async def test_channel_mode_reply_and_ban_list_numerics_update_state(
    minimal_config_dict: dict,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    channel = "#test"
    bot.channels[channel.lower()] = ChannelState(name=channel)

    await bot._handle_channel_mode_is(parse(":server 324 TestBot #test +nt"))
    await bot._handle_ban_list(parse(":server 367 TestBot #test trouble!*@* op 1713520000"))

    ch = bot.channels[channel.lower()]
    assert "n" in ch.modes
    assert "t" in ch.modes
    assert "trouble!*@*" in ch.bans


@pytest.mark.asyncio
async def test_who_reply_refreshes_user_and_host(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    channel = "#test"
    bot.channels[channel.lower()] = ChannelState(name=channel)

    await bot._handle_who_reply(
        parse(":server 352 TestBot #test ident host srv alice H :0 Real Name")
    )

    ns = bot.channels[channel.lower()].get_nick("alice")
    assert ns is not None
    assert ns.user == "ident"
    assert ns.host == "host"


@pytest.mark.asyncio
async def test_whois_dedup_in_flight_requests(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent: list[str] = []

    async def fake_send(line: str) -> None:
        sent.append(line)

    conn.send = fake_send  # type: ignore[method-assign]

    task1 = asyncio.create_task(conn.whois("Alice"))
    task2 = asyncio.create_task(conn.whois("Alice"))
    await asyncio.sleep(0)

    assert sent == ["WHOIS Alice"]

    await conn._on_whois_311(parse(":server 311 bot Alice user host * :Real Name"))
    await conn._on_whois_330(parse(":server 330 bot Alice accountname :is logged in as"))
    await conn._on_whois_318(parse(":server 318 bot Alice :End of /WHOIS list."))

    result1, result2 = await asyncio.gather(task1, task2)
    assert result1 == result2
    assert result1["user"] == "user"
    assert result1["host"] == "host"
    assert result1["account"] == "accountname"


@pytest.mark.asyncio
async def test_whois_uses_cache_within_ttl(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent: list[str] = []

    async def fake_send(line: str) -> None:
        sent.append(line)

    conn.send = fake_send  # type: ignore[method-assign]

    task = asyncio.create_task(conn.whois("Alice"))
    await asyncio.sleep(0)
    await conn._on_whois_311(parse(":server 311 bot Alice user host * :Real Name"))
    await conn._on_whois_330(parse(":server 330 bot Alice accountname :is logged in as"))
    await conn._on_whois_318(parse(":server 318 bot Alice :End of /WHOIS list."))
    first = await task

    second = await conn.whois("Alice")

    assert sent == ["WHOIS Alice"]
    assert first == second


@pytest.mark.asyncio
async def test_whois_timeout_returns_empty_result(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent: list[str] = []

    async def fake_send(line: str) -> None:
        sent.append(line)

    conn.send = fake_send  # type: ignore[method-assign]

    result = await conn.whois("Alice", timeout=0.01)
    assert result == {}
    assert sent == ["WHOIS Alice"]


@pytest.mark.asyncio
async def test_whois_cache_is_bounded(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)
    conn._whois_cache_max_entries = 2

    conn._whois_cache["one"] = (999999.0, {"account": "one"})
    conn._whois_cache["two"] = (999999.0, {"account": "two"})

    sent: list[str] = []

    async def fake_send(line: str) -> None:
        sent.append(line)

    conn.send = fake_send  # type: ignore[method-assign]

    task = asyncio.create_task(conn.whois("three"))
    await asyncio.sleep(0)
    await conn._on_whois_318(parse(":server 318 bot three :End of /WHOIS list."))
    await task

    assert len(conn._whois_cache) == 2
    assert "one" not in conn._whois_cache


@pytest.mark.asyncio
async def test_invalidate_whois_cache_removes_entry(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)
    conn._whois_cache["alice"] = (999999.0, {"account": "alice"})

    conn.invalidate_whois_cache("Alice")

    assert "alice" not in conn._whois_cache


@pytest.mark.asyncio
async def test_mode_takes_parameter_uses_chanmodes_groups(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)
    await conn._on_005(
        parse(
            ":irc.example.com 005 TestBot CHANMODES=beI,k,l,imnpst "
            "PREFIX=(ov)@+ :are supported by this server"
        )
    )

    assert conn.mode_takes_parameter("b", True)
    assert conn.mode_takes_parameter("k", True)
    assert conn.mode_takes_parameter("k", False)
    assert conn.mode_takes_parameter("l", True)
    assert not conn.mode_takes_parameter("l", False)
    assert not conn.mode_takes_parameter("m", True)


@pytest.mark.asyncio
async def test_cap_new_requests_new_desired_caps(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent_raw: list[str] = []

    async def fake_send_raw(line: str) -> None:
        sent_raw.append(line)

    conn.send_raw = fake_send_raw  # type: ignore[method-assign]
    conn._caps_acked.add("multi-prefix")

    await conn._on_cap(parse(":server CAP * NEW :multi-prefix account-notify draft/test"))

    assert conn._caps_available.issuperset({"multi-prefix", "account-notify", "draft/test"})
    assert sent_raw == ["CAP REQ :account-notify"]


@pytest.mark.asyncio
async def test_cap_del_prunes_available_and_acked(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)
    conn._caps_available.update({"account-notify", "chghost"})
    conn._caps_acked.update({"account-notify", "chghost"})

    await conn._on_cap(parse(":server CAP * DEL :account-notify"))

    assert "account-notify" not in conn._caps_available
    assert "account-notify" not in conn._caps_acked
    assert "chghost" in conn._caps_available
    assert "chghost" in conn._caps_acked


@pytest.mark.asyncio
async def test_build_trigger_uses_whois_account_fallback_for_commands(
    minimal_config_dict: dict,
) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    async def fake_whois(_nick: str) -> dict[str, str]:
        return {"account": "alice_account"}

    bot.whois = fake_whois  # type: ignore[method-assign]

    msg = parse(":alice!u@h PRIVMSG #test :!ping")
    trigger = await bot._build_trigger(msg, args=["dummy"], match=None)

    assert trigger is not None
    assert trigger.account == "alice_account"


def test_runtime_session_reset_clears_protocol_state(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    conn.isupport = {"NETWORK": "OldNet"}
    conn.network_name = "OldNet"
    conn.mode_to_prefix = {"q": "~"}
    conn.prefix_to_mode = {"~": "q"}
    conn.nick_prefix_chars = "~"
    conn.nick_prefix_modes = {"q"}
    conn.chanmodes = "beI,k,l,imnpst"
    conn.chanmodes_a = {"b", "e", "I"}
    conn.chanmodes_b = {"k"}
    conn.chanmodes_c = {"l"}
    conn.chanmodes_d = {"i", "m"}
    conn._caps_available = {"account-notify"}
    conn._caps_acked = {"account-notify"}
    conn._whois_cache = {"alice": (999999.0, {"account": "alice"})}

    conn._reset_runtime_session_state()

    assert conn.isupport == {}
    assert conn.network_name == ""
    assert conn.mode_to_prefix == {"o": "@", "v": "+"}
    assert conn.prefix_to_mode == {"@": "o", "+": "v"}
    assert conn.nick_prefix_chars == "@+"
    assert conn.nick_prefix_modes == {"o", "v"}
    assert conn.chanmodes == ""
    assert conn.chanmodes_a == {"b"}
    assert conn.chanmodes_b == set()
    assert conn.chanmodes_c == set()
    assert conn.chanmodes_d == set()
    assert conn._caps_available == set()
    assert conn._caps_acked == set()
    assert conn._whois_cache == {}


@pytest.mark.asyncio
async def test_runtime_session_reset_resolves_pending_whois(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    fut: asyncio.Future[dict[str, str]] = asyncio.get_event_loop().create_future()
    conn._whois_futures["alice"] = fut
    conn._whois_data["alice"] = {"account": "alice"}

    conn._reset_runtime_session_state()

    assert await fut == {}
    assert conn._whois_futures == {}
    assert conn._whois_data == {}


@pytest.mark.asyncio
async def test_welcome_updates_current_nick_from_server(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    await bot._dispatch(parse(":irc.example.com 001 AltBot :Welcome"))

    assert bot.nick == "AltBot"


@pytest.mark.asyncio
async def test_welcome_clears_stale_state_on_new_session(minimal_config_dict: dict) -> None:
    cfg = BotConfig.model_validate(minimal_config_dict)
    bot = PyraBot(cfg)

    bot.channels["#old"] = ChannelState(name="#old")
    bot._names_buffer["#old"] = ["stale"]

    await bot._dispatch(parse(":irc.example.com 001 TestBot :Welcome"))

    assert bot.channels == {}
    assert bot._names_buffer == {}


@pytest.mark.asyncio
async def test_build_trigger_owner_account_fallback_grants_owner(minimal_config_dict: dict) -> None:
    cfg_dict = dict(minimal_config_dict)
    cfg_dict["core"] = dict(minimal_config_dict["core"])
    cfg_dict["core"]["owner_account"] = "jarsky"
    cfg = BotConfig.model_validate(cfg_dict)
    bot = PyraBot(cfg)

    msg = parse("@account=Jarsky :anynick!u@h PRIVMSG #test :hello")
    trigger = await bot._build_trigger(msg, args=[], match=None)

    assert trigger is not None
    assert trigger.owner is True
    assert trigger.admin is True


@pytest.mark.asyncio
async def test_build_trigger_owner_account_fallback_does_not_grant_on_mismatch(
    minimal_config_dict: dict,
) -> None:
    cfg_dict = dict(minimal_config_dict)
    cfg_dict["core"] = dict(minimal_config_dict["core"])
    cfg_dict["core"]["owner_account"] = "jarsky"
    cfg = BotConfig.model_validate(cfg_dict)
    bot = PyraBot(cfg)

    msg = parse("@account=otheracct :anynick!u@h PRIVMSG #test :hello")
    trigger = await bot._build_trigger(msg, args=[], match=None)

    assert trigger is not None
    assert trigger.owner is False
    assert trigger.admin is False


@pytest.mark.asyncio
async def test_server_pass_and_nickserv_identify_both_apply(minimal_config_dict: dict) -> None:
    cfg_dict = dict(minimal_config_dict)
    cfg_dict["servers"] = [dict(minimal_config_dict["servers"][0])]
    cfg_dict["servers"][0]["password"] = "serverpass"
    cfg_dict["channels"] = {"autojoin": ["#chat"]}
    cfg_dict["auth"] = dict(minimal_config_dict["auth"])
    cfg_dict["auth"]["auth_method"] = "nickserv"
    cfg_dict["auth"]["nickserv_password"] = "nickpass"

    cfg = BotConfig.model_validate(cfg_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent_raw: list[str] = []
    sent_queued: list[str] = []

    async def fake_send_raw(line: str) -> None:
        sent_raw.append(line)

    async def fake_send(line: str) -> None:
        sent_queued.append(line)

    conn.send_raw = fake_send_raw  # type: ignore[method-assign]
    conn.send = fake_send  # type: ignore[method-assign]

    await conn._begin_registration()
    await conn._on_001(parse(":irc.example.com 001 TestBot :Welcome"))

    assert sent_raw[0] == "CAP LS 302"
    assert "PASS :serverpass" in sent_raw
    assert any(line.startswith("NICK ") for line in sent_raw)
    assert any(line.startswith("USER ") for line in sent_raw)
    assert "JOIN #chat" in sent_queued
    assert "PRIVMSG NickServ :IDENTIFY nickpass" in sent_queued


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("auth_method", "target", "command_text"),
    [
        ("authserv", "AuthServ@services.undernet.org", "AUTH svcacct nickpass"),
        ("q", "Q@CServe.quakenet.org", "AUTH svcacct nickpass"),
        ("userserv", "UserServ", "LOGIN svcacct nickpass"),
    ],
)
async def test_server_pass_and_service_auth_variants_both_apply(
    minimal_config_dict: dict,
    auth_method: str,
    target: str,
    command_text: str,
) -> None:
    cfg_dict = dict(minimal_config_dict)
    cfg_dict["servers"] = [dict(minimal_config_dict["servers"][0])]
    cfg_dict["servers"][0]["password"] = "serverpass"
    cfg_dict["channels"] = {"autojoin": ["#chat"]}
    cfg_dict["auth"] = dict(minimal_config_dict["auth"])
    cfg_dict["auth"]["auth_method"] = auth_method
    cfg_dict["auth"]["nickserv_password"] = "nickpass"
    cfg_dict["auth"]["sasl_username"] = "svcacct"

    cfg = BotConfig.model_validate(cfg_dict)
    conn = IRCConnection(cfg, lambda _msg: None)

    sent_raw: list[str] = []
    sent_queued: list[str] = []

    async def fake_send_raw(line: str) -> None:
        sent_raw.append(line)

    async def fake_send(line: str) -> None:
        sent_queued.append(line)

    conn.send_raw = fake_send_raw  # type: ignore[method-assign]
    conn.send = fake_send  # type: ignore[method-assign]

    await conn._begin_registration()
    await conn._on_001(parse(":irc.example.com 001 TestBot :Welcome"))

    assert "PASS :serverpass" in sent_raw
    assert "JOIN #chat" in sent_queued
    assert f"PRIVMSG {target} :{command_text}" in sent_queued
