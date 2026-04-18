"""Tests for the configuration system."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from pybot.core.config import BotConfig, ConfigError, load_config
from tests.conftest import MINIMAL_CONFIG


def test_load_minimal_config(minimal_config_file: Path) -> None:
    config = load_config(minimal_config_file)
    assert config.core.nick == "TestBot"
    assert config.servers[0].host == "irc.example.com"
    assert config.servers[0].port == 6697


def test_load_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nonexistent.yaml")


def test_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [invalid yaml\n")
    with pytest.raises(ConfigError):
        load_config(bad)


def test_missing_servers(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    data = {
        "core": {"nick": "Bot"},
        "servers": [],  # empty — must fail
        "web": {"enabled": False, "secret_key": ""},
    }
    cfg.write_text(yaml.dump(data))
    with pytest.raises(ConfigError, match="server"):
        load_config(cfg)


def test_secret_str_not_in_repr(minimal_config_file: Path) -> None:
    config = load_config(minimal_config_file)
    # SecretStr values must not appear in repr/str
    # sasl_password default is empty — just verify SecretStr type hides it
    assert "**" in repr(config.auth.sasl_password) or "SecretStr" in repr(config.auth.sasl_password)


def test_invalid_log_level(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    import yaml as _yaml

    data = {"core": {"nick": "Bot", "log_level": "INVALID"}, "servers": [{"host": "x"}]}
    cfg.write_text(_yaml.dump(data))
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_invalid_port(tmp_path: Path) -> None:
    cfg = tmp_path / "config.yaml"
    import yaml as _yaml

    data = {"core": {}, "servers": [{"host": "x", "port": 99999}]}
    cfg.write_text(_yaml.dump(data))
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_primary_server_priority(minimal_config_dict: dict) -> None:
    minimal_config_dict["servers"] = [
        {"host": "b.example.com", "port": 6697, "ssl": False, "priority": 2},
        {"host": "a.example.com", "port": 6697, "ssl": False, "priority": 1},
    ]
    config = BotConfig.model_validate(minimal_config_dict)
    assert config.primary_server.host == "a.example.com"


def test_sasl_external_requires_certfile(minimal_config_dict: dict) -> None:
    minimal_config_dict["auth"] = {
        "sasl_mechanism": "EXTERNAL",
        "certfile": "",  # missing — should fail
    }
    with pytest.raises(Exception, match="certfile"):
        BotConfig.model_validate(minimal_config_dict)


def test_web_disabled_no_secret_ok(minimal_config_dict: dict) -> None:
    minimal_config_dict["web"] = {"enabled": False, "secret_key": ""}
    config = BotConfig.model_validate(minimal_config_dict)
    assert not config.web.enabled


def test_web_enabled_with_empty_secret_key_is_valid(minimal_config_dict: dict) -> None:
    """Empty secret_key is accepted at model level; auto-generation happens in load_config."""
    minimal_config_dict["web"] = {"enabled": True, "secret_key": ""}
    config = BotConfig.model_validate(minimal_config_dict)
    assert config.web.enabled


def test_web_secret_key_auto_generated(tmp_path: Path) -> None:
    """load_config auto-generates and persists secret_key when web is enabled and key is empty."""
    cfg = copy.deepcopy(MINIMAL_CONFIG)
    cfg["web"] = {"enabled": True, "secret_key": ""}
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(cfg))

    config = load_config(config_path)

    generated = config.web.secret_key.get_secret_value()
    assert generated  # non-empty key was generated
    saved = yaml.safe_load(config_path.read_text())
    assert saved["web"]["secret_key"] == generated  # persisted to file


def test_command_prefix_validation(minimal_config_dict: dict) -> None:
    minimal_config_dict["core"]["command_prefix"] = "toolong!!!!"
    with pytest.raises(Exception):
        BotConfig.model_validate(minimal_config_dict)


def test_database_url_env_override(
    monkeypatch: pytest.MonkeyPatch, minimal_config_file: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://pyra:secret@postgres:5432/pyra")

    config = load_config(minimal_config_file)

    assert config.database.url == "postgresql+asyncpg://pyra:secret@postgres:5432/pyra"
