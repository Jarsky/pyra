"""Pyra — Modern Python IRC Bot."""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Jarsky"
__license__ = "MIT"

from pybot import plugin as plugin  # re-export for `from pybot import plugin`

__all__ = ["__version__", "plugin"]
