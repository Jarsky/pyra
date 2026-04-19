"""Configuration loader and validator using Pydantic v2."""

from __future__ import annotations

import copy
import ipaddress
import os
import secrets
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class ConfigError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class CoreConfig(BaseModel):
    nick: str = "Pyra"
    altnicks: list[str] = Field(default_factory=lambda: ["Pyra_", "Pyra__"])
    realname: str = "Pyra IRC Bot"
    ident: str = "pyra"
    command_prefix: str = "!"
    owner: str = ""
    owner_account: str = ""
    admins: list[str] = Field(default_factory=list)
    log_level: str = "INFO"
    log_file: str = "data/logs/pyra.log"
    log_rotate: bool = True

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {allowed}")
        return upper

    @field_validator("command_prefix")
    @classmethod
    def validate_prefix(cls, v: str) -> str:
        if not v or len(v) > 3:
            raise ValueError("command_prefix must be 1-3 characters")
        return v


class ServerConfig(BaseModel):
    host: str
    port: int = 6697
    ssl: bool = True
    ssl_verify: bool = True
    password: SecretStr = SecretStr("")
    priority: int = 1

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v


class AuthConfig(BaseModel):
    # Primary auth method used after registration.
    # sasl uses the sasl_mechanism field; nickserv/authserv/q/userserv send
    # a PRIVMSG to the respective service with the nickserv_password.
    # server_password sends it as the server PASS during connection.
    # none disables service authentication entirely.
    auth_method: Literal[
        "sasl", "nickserv", "authserv", "q", "userserv", "server_password", "none"
    ] = "none"
    sasl_mechanism: Literal["PLAIN", "EXTERNAL", "SCRAM-SHA-256", "none"] = "none"
    sasl_username: str = ""
    sasl_password: SecretStr = SecretStr("")
    # Kept for backwards compatibility — equivalent to auth_method = "nickserv"
    nickserv_identify: bool = False
    nickserv_password: SecretStr = SecretStr("")
    certfile: str = ""
    keyfile: str = ""

    @model_validator(mode="after")
    def validate_external_cert(self) -> "AuthConfig":
        if self.sasl_mechanism == "EXTERNAL" and not self.certfile:
            raise ValueError("certfile is required when sasl_mechanism is EXTERNAL")
        # Normalise legacy nickserv_identify bool to auth_method
        if self.nickserv_identify and self.auth_method == "none":
            self.auth_method = "nickserv"
        return self


class ChannelsConfig(BaseModel):
    autojoin: list[str] = Field(default_factory=list)
    channel_config: dict[str, dict[str, Any]] = Field(default_factory=dict)


class DatabaseConfig(BaseModel):
    url: str = "sqlite+aiosqlite:///data/pyra.db"
    echo: bool = False


class FloodConfig(BaseModel):
    lines: int = 5
    seconds: int = 2
    burst: int = 3
    punishment: Literal["none", "kick", "ban", "tempban"] = "kick"


class PartylineConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 3333
    password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def warn_if_exposed(self) -> "PartylineConfig":
        if self.enabled and self.host == "0.0.0.0":  # noqa: S104
            import warnings

            warnings.warn(
                "Partyline is bound to 0.0.0.0 — this exposes the admin console to "
                "the network. Strongly recommend binding to 127.0.0.1.",
                stacklevel=2,
            )
        return self


class WebConfig(BaseModel):
    enabled: bool = True
    host: str = "0.0.0.0"  # noqa: S104
    port: int = 8080
    secret_key: SecretStr = SecretStr("")
    debug: bool = False
    session_timeout: int = 28800
    trusted_proxies: list[str] = Field(default_factory=lambda: ["127.0.0.1", "::1"])

    @field_validator("trusted_proxies")
    @classmethod
    def validate_trusted_proxies(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value:
                raise ValueError("trusted_proxies entries cannot be empty")
            if value == "*":
                normalized.append(value)
                continue
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError as exc:
                raise ValueError(
                    f"trusted_proxies entry must be IP/CIDR or '*': {value!r}"
                ) from exc
            normalized.append(value)
        return normalized


class PluginsConfig(BaseModel):
    enabled: list[str] | Literal["all"] = "all"
    disabled: list[str] = Field(default_factory=list)
    extra_dir: str = "/plugins_extra"
    vars: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ServicesConfig(BaseModel):
    enabled: bool = False
    chanserv_op: bool = True
    vhost: str = ""
    # Raw IRC lines to send after registration and auth settle (e.g. "MODE botnick +x")
    commands_on_connect: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Root config model
# ---------------------------------------------------------------------------


class BotConfig(BaseModel):
    core: CoreConfig = Field(default_factory=CoreConfig)
    servers: list[ServerConfig] = Field(default_factory=list)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    flood: FloodConfig = Field(default_factory=FloodConfig)
    partyline: PartylineConfig = Field(default_factory=PartylineConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)

    @model_validator(mode="after")
    def require_at_least_one_server(self) -> "BotConfig":
        if not self.servers:
            raise ValueError("At least one server must be configured under 'servers:'")
        return self

    @property
    def primary_server(self) -> ServerConfig:
        return sorted(self.servers, key=lambda s: s.priority)[0]


# ---------------------------------------------------------------------------
# Load / save helpers
# ---------------------------------------------------------------------------


def load_config(path: Path) -> BotConfig:
    """Load and validate config from a YAML file."""
    if not path.exists():
        raise ConfigError(
            f"Config file not found: {path}\n"
            "Run 'pybot-setup' to create one, or copy config/config.example.yaml."
        )
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config YAML: {exc}") from exc

    # Auto-generate secret_key on first run so the web interface works out of the box.
    web_cfg = raw.get("web") or {}
    if web_cfg.get("enabled", True) and not web_cfg.get("secret_key", ""):
        if "web" not in raw:
            raw["web"] = {}
        raw["web"]["secret_key"] = secrets.token_hex(32)
        path.write_text(yaml.dump(raw, default_flow_style=False), encoding="utf-8")

    try:
        config = BotConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Invalid configuration: {exc}") from exc

    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        config.database.url = database_url

    return config


def save_config_partial(path: Path, config: BotConfig, updates: dict[str, Any]) -> BotConfig:
    """Deep-merge updates into the config, re-validate, and write to disk."""
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    else:
        raw = config.model_dump(mode="json")

    raw = _deep_merge(raw, updates)

    try:
        new_config = BotConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Updated configuration is invalid: {exc}") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(raw, default_flow_style=False), encoding="utf-8")
    return new_config


def _deep_merge(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result
