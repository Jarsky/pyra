"""Pyra IRC bot entry point."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pybot",
        description="Pyra — Modern Python IRC Bot",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_get_version()}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/config.yaml"),
        help="Path to config.yaml (default: config/config.yaml)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


def _get_version() -> str:
    from pybot import __version__

    return __version__


def main() -> None:
    args = _parse_args()

    # Phase 1+: actual bot startup
    try:
        from pybot.core.config import ConfigError, load_config

        config = load_config(args.config)
    except ImportError:
        # Phase 0: core modules not yet implemented
        print(f"Pyra {_get_version()} — run pybot-setup to configure")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.debug:
        config.core.log_level = "DEBUG"

    import asyncio

    from pybot.core.bot import PyraBot

    bot = PyraBot(config)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
