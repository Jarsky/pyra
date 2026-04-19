"""Tests for IRCMessage.parse()."""

from __future__ import annotations

import pytest

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.irc import IRCConnection
from pybot.core.irc import IRCMessage


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
