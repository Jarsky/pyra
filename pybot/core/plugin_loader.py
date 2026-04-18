"""
Dynamic plugin loader with hot-reload support.

Plugins are discovered from directories, loaded via importlib,
and registered via the pybot.plugin decorator registry.
Hot-reload: file mtimes are polled every 5 seconds; SIGHUP triggers reload_all().
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from loguru import logger

from pybot.plugin import _set_current_plugin, get_registry


class PluginLoader:
    def __init__(self, bot: object) -> None:
        self._bot = bot
        self._loaded: dict[str, ModuleType] = {}  # name -> module
        self._paths: dict[str, Path] = {}  # name -> file path
        self._mtimes: dict[str, float] = {}  # name -> last mtime
        self._watch_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, name: str, path: Path) -> None:
        """Load a single plugin from a file path."""
        if name in self._loaded:
            await self.unload(name)

        logger.debug(f"Loading plugin: {name} from {path}")

        # Tell the registry which plugin is being loaded
        _set_current_plugin(name)

        module_name = f"pybot.plugins._loaded.{name}"

        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create spec for {path}")

            module = importlib.util.module_from_spec(spec)
            # Register in sys.modules so relative imports work.
            sys.modules[module_name] = module

            # Invalidate import caches and drop stale bytecode to make rapid reloads reliable.
            importlib.invalidate_caches()
            pyc_path = Path(importlib.util.cache_from_source(str(path)))
            if pyc_path.exists():
                pyc_path.unlink()

            spec.loader.exec_module(module)  # type: ignore[union-attr]

            self._loaded[name] = module
            self._paths[name] = path
            self._mtimes[name] = path.stat().st_mtime

            # Call plugin setup() if defined
            if hasattr(module, "setup") and callable(module.setup):
                await module.setup(self._bot)

            logger.info(f"Plugin loaded: {name}")
        except Exception as exc:
            logger.error(f"Failed to load plugin '{name}': {exc}")
            # Ensure registry is cleaned up on partial load
            get_registry().clear_plugin(name)
            sys.modules.pop(module_name, None)
        finally:
            _set_current_plugin("unknown")

    async def unload(self, name: str) -> None:
        """Unload a plugin and remove all its registered handlers."""
        if name not in self._loaded:
            logger.warning(f"Plugin '{name}' is not loaded")
            return

        module = self._loaded[name]

        # Call plugin shutdown() if defined
        if hasattr(module, "shutdown") and callable(module.shutdown):
            try:
                await module.shutdown(self._bot)
            except Exception as exc:
                logger.error(f"Plugin '{name}' shutdown() error: {exc}")

        get_registry().clear_plugin(name)
        sys.modules.pop(f"pybot.plugins._loaded.{name}", None)
        del self._loaded[name]
        self._paths.pop(name, None)
        self._mtimes.pop(name, None)

        logger.info(f"Plugin unloaded: {name}")

    async def reload(self, name: str) -> None:
        """Reload a plugin by name."""
        if name not in self._paths:
            raise KeyError(f"Plugin '{name}' is not known (never loaded)")
        path = self._paths[name]
        await self.unload(name)
        await self.load(name, path)

    async def reload_all(self) -> None:
        """Reload all currently loaded plugins."""
        names = list(self._loaded.keys())
        for name in names:
            await self.reload(name)

    async def load_all(self, directories: list[Path]) -> None:
        """Discover and load all .py plugins from a list of directories."""
        plugins_config = getattr(getattr(self._bot, "config", None), "plugins", None)
        enabled = getattr(plugins_config, "enabled", "all") if plugins_config else "all"
        disabled = list(getattr(plugins_config, "disabled", []) if plugins_config else [])

        for directory in directories:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.py")):
                name = path.stem
                if name.startswith("_"):
                    continue
                if name in disabled:
                    logger.debug(f"Skipping disabled plugin: {name}")
                    continue
                if enabled != "all" and name not in enabled:
                    logger.debug(f"Skipping non-enabled plugin: {name}")
                    continue
                await self.load(name, path)

        # Start the file watcher
        self._watch_task = asyncio.create_task(self._watch_for_changes(), name="plugin-watcher")

    def get_loaded_plugins(self) -> dict[str, Path]:
        return dict(self._paths)

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded

    # ------------------------------------------------------------------
    # Hot-reload file watcher
    # ------------------------------------------------------------------

    async def _watch_for_changes(self) -> None:
        """Poll file mtimes every 5 seconds and reload changed plugins."""
        while True:
            await asyncio.sleep(5)
            for name, path in list(self._paths.items()):
                try:
                    current_mtime = path.stat().st_mtime
                    if current_mtime != self._mtimes.get(name):
                        logger.info(f"Plugin '{name}' changed on disk — reloading")
                        await self.reload(name)
                except FileNotFoundError:
                    logger.warning(f"Plugin file missing: {path} — unloading '{name}'")
                    await self.unload(name)
                except Exception as exc:
                    logger.error(f"Watcher error for plugin '{name}': {exc}")
