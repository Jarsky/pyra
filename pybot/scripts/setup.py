"""pybot-setup — interactive first-run wizard."""

from __future__ import annotations

import asyncio
import copy
import getpass
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# Keys must match pybot.core.config (unknown keys are silently ignored by the loader)
CONFIG_TEMPLATE = {
    "core": {
        "nick": "",
        "altnicks": [],
        "ident": "pyra",
        "realname": "Pyra IRC Bot",
        "command_prefix": "!",
        "owner": "",
        "owner_account": "",
        "owner_hostmask": "",
        "log_level": "INFO",
        "log_file": "data/pyra.log",
    },
    "servers": [
        {
            "host": "",
            "port": 6697,
            "ssl": True,
            "priority": 1,
        }
    ],
    "auth": {
        "auth_method": "none",
        "sasl_mechanism": "none",
        "sasl_username": "",
        "sasl_password": "",
        "nickserv_password": "",
    },
    "channels": {
        "autojoin": [],
    },
    "database": {
        "url": "sqlite+aiosqlite:///data/pyra.db",
    },
    "partyline": {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 3333,
    },
    "web": {
        "enabled": True,
        "host": "0.0.0.0",  # noqa: S104
        "port": 8080,
        "secret_key": "",
    },
    "plugins": {
        "extra_dir": "/plugins_extra" if Path("/.dockerenv").exists() else "plugins_extra",
        "disabled": [],
    },
}

# Wizard choice -> (auth_method, sasl_mechanism)
AUTH_CHOICES = {
    "none": ("none", "none"),
    "nickserv": ("nickserv", "none"),
    "sasl_plain": ("sasl", "PLAIN"),
    "sasl_scram": ("sasl", "SCRAM-SHA-256"),
    "sasl_external": ("sasl", "EXTERNAL"),
}


def apply_auth_choice(cfg: dict[str, Any], choice: str, nick: str, password: str = "") -> None:
    """Fill cfg["auth"] for a wizard auth choice (raises KeyError for unknown choices)."""
    auth_method, mechanism = AUTH_CHOICES[choice]
    cfg["auth"]["auth_method"] = auth_method
    cfg["auth"]["sasl_mechanism"] = mechanism
    if choice == "nickserv":
        cfg["auth"]["nickserv_password"] = password
    elif mechanism in ("PLAIN", "SCRAM-SHA-256"):
        cfg["auth"]["sasl_username"] = nick
        cfg["auth"]["sasl_password"] = password


def owner_mask_problem(mask: str) -> str | None:
    """Why a hostmask is unsafe for the owner, or None if it is acceptable."""
    from pybot.core.permissions import is_nick_only_mask

    if mask and is_nick_only_mask(mask):
        return (
            f"'{mask}' would match anyone who uses that nick (or anyone at all). "
            "Use nick!user@host, e.g. *!myident@my.host.example, or leave it blank "
            "and rely on your services account."
        )
    return None


def _prompt(msg: str, default: str = "", secret: bool = False) -> str:
    if default:
        display = f"{msg} [{default}]: "
    else:
        display = f"{msg}: "
    if secret:
        val = getpass.getpass(display)
    else:
        val = input(display).strip()
    return val or default


def _section(title: str) -> None:
    print(f"\n\033[1;36m{'─' * 50}\033[0m")
    print(f"\033[1;36m  {title}\033[0m")
    print(f"\033[1;36m{'─' * 50}\033[0m")


def _success(msg: str) -> None:
    print(f"\033[1;32m✓ {msg}\033[0m")


def _warn(msg: str) -> None:
    print(f"\033[1;33m⚠ {msg}\033[0m")


def _generate_secret_key() -> str:
    import secrets

    return secrets.token_hex(32)


async def _init_db_and_owner(
    config_path: Path,
    owner_nick: str,
    owner_host: str,
    owner_password: str,
    owner_account: str = "",
) -> None:
    from pybot.core.config import load_config
    from pybot.core.database import get_session, init_db
    from pybot.core.permissions import add_owner_bootstrap
    from pybot.web.auth import hash_password

    config = load_config(config_path)
    await init_db(config.database.url, echo=False)

    async with get_session() as session:
        hashed = hash_password(owner_password)
        await add_owner_bootstrap(
            session, owner_nick, owner_host, hashed, account=owner_account or None
        )
        await session.commit()


def _write_systemd_unit(config_path: Path) -> None:
    unit = f"""[Unit]
Description=Pyra IRC Bot
After=network.target

[Service]
Type=simple
User={os.getenv('USER', 'pyra')}
WorkingDirectory={config_path.parent.parent}
ExecStart=pybot --config {config_path}
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
"""
    unit_path = config_path.parent / "pyra.service"
    unit_path.write_text(unit)
    print(f"\n  Systemd unit written to: {unit_path}")
    print("  To install:")
    print(f"    sudo cp {unit_path} /etc/systemd/system/")
    print("    sudo systemctl daemon-reload")
    print("    sudo systemctl enable --now pyra")


def main() -> None:
    print("\033[1;32m")
    print("  ██████╗ ██╗   ██╗██████╗  █████╗ ")
    print("  ██╔══██╗╚██╗ ██╔╝██╔══██╗██╔══██╗")
    print("  ██████╔╝ ╚████╔╝ ██████╔╝███████║")
    print("  ██╔═══╝   ╚██╔╝  ██╔══██╗██╔══██║")
    print("  ██║        ██║   ██║  ██║██║  ██║")
    print("  ╚═╝        ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝")
    print("\033[0m")
    print("  Pyra IRC Bot — Setup Wizard")
    print("  ─────────────────────────────────")

    # Config output path
    _section("Configuration File")
    default_config = "/data/config.yaml" if Path("/.dockerenv").exists() else "config/config.yaml"
    config_input = _prompt("Config file path", default_config).strip()
    candidate = Path(config_input)
    if candidate.suffix.lower() in {".yaml", ".yml"}:
        config_path = candidate
        config_dir = config_path.parent
    else:
        config_dir = candidate
        config_path = config_dir / "config.yaml"

    config_dir.mkdir(parents=True, exist_ok=True)
    if config_path.exists():
        overwrite = _prompt(f"{config_path} already exists. Overwrite? (yes/no)", "no")
        if overwrite.lower() not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    cfg: dict[str, Any] = copy.deepcopy(CONFIG_TEMPLATE)

    # Bot identity
    _section("Bot Identity")
    cfg["core"]["nick"] = _prompt("Bot nick", "Pyra")
    cfg["core"]["ident"] = _prompt("Ident", cfg["core"]["nick"].lower())
    cfg["core"]["realname"] = _prompt("Real name", "Pyra IRC Bot")
    cfg["core"]["command_prefix"] = _prompt("Command prefix", "!")

    # Server
    _section("IRC Server")
    cfg["servers"][0]["host"] = _prompt("Server hostname", "irc.libera.chat")
    cfg["servers"][0]["port"] = int(_prompt("Port", "6697"))
    ssl_ans = _prompt("Use SSL/TLS? (yes/no)", "yes")
    cfg["servers"][0]["ssl"] = ssl_ans.lower() in ("yes", "y", "true", "1")

    # Auth
    _section("Authentication")
    print(f"  Methods: {', '.join(AUTH_CHOICES)}")
    auth_choice = _prompt("Auth method", "none")
    while auth_choice not in AUTH_CHOICES:
        auth_choice = _prompt(f"Choose one of {', '.join(AUTH_CHOICES)}", "none")
    auth_password = ""
    if auth_choice in ("nickserv", "sasl_plain", "sasl_scram"):
        auth_password = _prompt("NickServ/SASL password", secret=True)
    apply_auth_choice(cfg, auth_choice, cfg["core"]["nick"], auth_password)
    if auth_choice == "sasl_external":
        cfg["auth"]["certfile"] = _prompt("Client certificate file (PEM)")
        cfg["auth"]["keyfile"] = _prompt("Private key file (blank if in the certificate)", "")

    # Channels
    _section("Channels to Auto-Join")
    channels_str = _prompt("Channels (comma-separated, e.g. #general,#bots)", "")
    cfg["channels"]["autojoin"] = [c.strip() for c in channels_str.split(",") if c.strip()]

    # Database
    _section("Database")
    print("  Options: sqlite (default), postgresql")
    db_type = _prompt("Database type", "sqlite")
    if db_type == "postgresql":
        db_host = _prompt("PostgreSQL host", "localhost")
        db_port = _prompt("PostgreSQL port", "5432")
        db_user = _prompt("PostgreSQL user", "pyra")
        db_pass = _prompt("PostgreSQL password", secret=True)
        db_name = _prompt("PostgreSQL database", "pyra")
        cfg["database"][
            "url"
        ] = f"postgresql+asyncpg://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    else:
        default_data_dir = "/data" if Path("/.dockerenv").exists() else "data"
        data_dir = Path(_prompt("Data directory (for SQLite + logs)", default_data_dir))
        data_dir.mkdir(parents=True, exist_ok=True)
        cfg["database"]["url"] = f"sqlite+aiosqlite:///{data_dir}/pyra.db"
        cfg["core"]["log_file"] = str(data_dir / "pyra.log")

    # Web UI
    _section("Web Interface")
    web_ans = _prompt("Enable web interface? (yes/no)", "yes")
    cfg["web"]["enabled"] = web_ans.lower() in ("yes", "y", "true", "1")
    if cfg["web"]["enabled"]:
        cfg["web"]["port"] = int(_prompt("Web UI port", "8080"))
        cfg["web"]["secret_key"] = _generate_secret_key()
        _success("Generated random JWT secret key")

    # Owner account
    _section("Owner Account")
    print("  This account will have full bot control (flag 'n').")
    print("  On IRC you are recognised by your services account and/or a hostmask,")
    print("  never by nick alone (anyone can take a nick).")
    owner_nick = _prompt("Your IRC nick (also your web/partyline username)")
    owner_account = _prompt("Your NickServ/services account (recommended)", "")
    while True:
        owner_host = _prompt("Your hostmask, e.g. *!ident@your.host (blank = account only)", "")
        problem = owner_mask_problem(owner_host)
        if problem is None:
            break
        _warn(problem)
    if not owner_account and not owner_host:
        _warn("No account or hostmask: owner commands on IRC won't work until you set one.")
    cfg["core"]["owner"] = owner_nick
    cfg["core"]["owner_account"] = owner_account
    cfg["core"]["owner_hostmask"] = owner_host
    owner_password = _prompt("Partyline/web password", secret=True)
    owner_confirm = _prompt("Confirm password", secret=True)
    if owner_password != owner_confirm:
        print("\033[1;31mPasswords do not match. Aborted.\033[0m")
        sys.exit(1)

    # Write config
    _section("Writing Configuration")
    with open(config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    _success(f"Config written to {config_path}")

    # Init DB + owner
    print("\n  Initialising database and owner account...")
    try:
        asyncio.run(
            _init_db_and_owner(config_path, owner_nick, owner_host, owner_password, owner_account)
        )
        _success("Database initialised")
        parts = [f"account {owner_account}" if owner_account else "", owner_host]
        identity = ", ".join(p for p in parts if p)
        _success(f"Owner account created for {owner_nick} ({identity or 'web/partyline only'})")
    except Exception as e:
        _warn(f"DB init failed: {e}")
        _warn("You can run 'alembic upgrade head' manually after fixing the issue.")

    # Systemd
    _section("Systemd Service (optional)")
    systemd_ans = _prompt("Generate systemd service file? (yes/no)", "no")
    if systemd_ans.lower() in ("yes", "y"):
        _write_systemd_unit(config_path)

    # Done
    print(f"\n\033[1;32m{'═' * 50}\033[0m")
    print("\033[1;32m  Setup complete! Next steps:\033[0m")
    print(f"\033[1;32m{'═' * 50}\033[0m\n")
    print(f"  Start the bot:   pybot --config {config_path}")
    if cfg["web"]["enabled"]:
        print(f"  Web interface:   http://localhost:{cfg['web']['port']}")
    if cfg["partyline"]["enabled"]:
        print(f"  Partyline:       telnet {cfg['partyline']['host']} {cfg['partyline']['port']}")
    print()


if __name__ == "__main__":
    main()
