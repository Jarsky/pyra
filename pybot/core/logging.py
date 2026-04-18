"""Logging setup using loguru."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from pybot.core.config import BotConfig


def setup_logging(config: "BotConfig") -> None:
    """Configure loguru sinks based on BotConfig."""
    logger.remove()  # Remove default handler

    level = config.core.log_level

    # Console sink
    logger.add(
        sys.stderr,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )

    # File sink (optional)
    if config.core.log_file:
        from pathlib import Path

        log_path = Path(config.core.log_file)
        if not log_path.is_absolute():
            # In containers DATA_DIR is typically /data; map relative paths there.
            data_dir = Path(os.environ.get("DATA_DIR", "data"))
            if log_path.parts and log_path.parts[0] == "data":
                remainder = Path(*log_path.parts[1:]) if len(log_path.parts) > 1 else Path()
                log_path = data_dir / remainder
            else:
                log_path = data_dir / log_path

        log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            str(log_path),
            level=level,
            rotation="10 MB" if config.core.log_rotate else None,
            retention="30 days" if config.core.log_rotate else None,
            compression="gz" if config.core.log_rotate else None,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | " "{name}:{function}:{line} - {message}"
            ),
            encoding="utf-8",
        )
