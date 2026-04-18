"""pybot-setup — interactive first-run wizard."""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from pathlib import Path

import yaml

CONFIG_TEMPLATE = {
    "core": {
        "nick": "",
        "alt_nicks": [],
        "ident": "pyra",
        "realname": "Pyra IRC Bot",
        "command_prefix": "!",
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
        "method": "none",
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
        "dirs": ["pybot/plugins", "plugins_extra"],
        "disabled": [],
    },
    "logging": {
        "level": "INFO",
        "file": "data/pyra.log",
        "rotation": "10 MB",
        "retention": "30 days",
    },
}


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
    config_path: Path, owner_nick: str, owner_host: str, owner_password: str
) -> None:
    import bcrypt

    from pybot.core.config import load_config
    from pybot.core.database import get_session, init_db
    from pybot.core.permissions import add_owner_bootstrap

    config = load_config(config_path)
    await init_db(config.database.url, echo=False)

    async with get_session() as session:
        hashed = bcrypt.hashpw(owner_password.encode(), bcrypt.gensalt()).decode()
        await add_owner_bootstrap(session, owner_nick, owner_host, hashed)
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
    print("\033[1;35m")
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
    config_dir = Path(_prompt("Config directory", "config"))
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    if config_path.exists():
        overwrite = _prompt(f"{config_path} already exists. Overwrite? (yes/no)", "no")
        if overwrite.lower() not in ("yes", "y"):
            print("Aborted.")
            sys.exit(0)

    cfg = CONFIG_TEMPLATE.copy()
    cfg["core"] = dict(cfg["core"])
    cfg["servers"] = [dict(cfg["servers"][0])]

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
    print("  Methods: none, nickserv, sasl_plain, sasl_external, sasl_scram")
    auth_method = _prompt("Auth method", "none")
    cfg["auth"]["method"] = auth_method
    if auth_method in ("nickserv", "sasl_plain", "sasl_scram"):
        cfg["auth"]["nickserv_password"] = _prompt("NickServ/SASL password", secret=True)

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
        data_dir = Path(_prompt("Data directory (for SQLite + logs)", "data"))
        data_dir.mkdir(parents=True, exist_ok=True)
        cfg["database"]["url"] = f"sqlite+aiosqlite:///{data_dir}/pyra.db"
        cfg["logging"]["file"] = str(data_dir / "pyra.log")

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
    owner_nick = _prompt("Your IRC nick")
    owner_host = _prompt("Your hostmask pattern (e.g. *!user@host.example.com)", "*!*@*")
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
        asyncio.run(_init_db_and_owner(config_path, owner_nick, owner_host, owner_password))
        _success("Database initialised")
        _success(f"Owner account created for {owner_nick} ({owner_host})")
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
