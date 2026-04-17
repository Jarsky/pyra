"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Minimal valid config dict for tests
# ---------------------------------------------------------------------------

MINIMAL_CONFIG: dict = {
    "core": {
        "nick": "TestBot",
        "realname": "Test Bot",
        "ident": "testbot",
        "command_prefix": "!",
        "owner": "TestOwner",
        "log_level": "DEBUG",
        "log_file": "",
        "log_rotate": False,
    },
    "servers": [
        {
            "host": "irc.example.com",
            "port": 6697,
            "ssl": True,
            "ssl_verify": False,
            "password": "",
            "priority": 1,
        }
    ],
    "auth": {
        "sasl_mechanism": "none",
        "sasl_username": "",
        "sasl_password": "",
        "nickserv_identify": False,
        "nickserv_password": "",
        "certfile": "",
        "keyfile": "",
    },
    "channels": {
        "autojoin": ["#test"],
    },
    "database": {
        "url": "sqlite+aiosqlite:///:memory:",
    },
    "flood": {
        "lines": 5,
        "seconds": 2,
        "burst": 3,
        "punishment": "kick",
    },
    "partyline": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 3333,
        "password": "testpass",
    },
    "web": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 8080,
        "secret_key": "testsecret",
        "debug": False,
        "session_timeout": 28800,
    },
    "plugins": {
        "enabled": [],
        "disabled": [],
        "extra_dir": "",
    },
    "services": {
        "enabled": False,
        "chanserv_op": False,
        "vhost": "",
    },
}


@pytest.fixture
def minimal_config_dict() -> dict:
    return dict(MINIMAL_CONFIG)


@pytest.fixture
def minimal_config_file(tmp_path: Path) -> Path:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(MINIMAL_CONFIG))
    return config_path
