"""
Dynamic plugin loader with hot-reload support.

Plugins are discovered from directories, loaded via importlib,
and registered via the pybot.plugin decorator registry.
Hot-reload: file mtimes are polled every 5 seconds; SIGHUP triggers reload_all().

Reloads are safe: the new version is executed before the old one is removed, so a
broken edit leaves the working version running. Files that fail to load stay watched
and are retried when they change.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import sys
from pathlib import Path
from types import ModuleType

from loguru import logger

from pybot.plugin import _set_current_plugin, get_registry


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded."""


def _module_name(name: str) -> str:
    return f"pybot.plugins._loaded.{name}"


class PluginLoader:
    def __init__(self, bot: object) -> None:
        self._bot = bot
        self._loaded: dict[str, ModuleType] = {}  # name -> module
        self._paths: dict[str, Path] = {}  # name -> file path (loaded plugins)
        self._available_paths: dict[str, Path] = {}  # discovered plugins, loaded or not
        self._failed: dict[str, Path] = {}  # plugins whose last load failed (still watched)
        self._errors: dict[str, str] = {}  # name -> last load error
        self._mtimes: dict[str, float] = {}  # name -> last mtime (loaded and failed)
        self._watch_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def load(self, name: str, path: Path) -> None:
        """Load (or replace) a plugin from a file path. Raises PluginLoadError on failure.

        If the plugin is already loaded and the new version fails, the old version keeps
        running.
        """
        self._available_paths[name] = path
        self._record_mtime(name, path)
        logger.debug(f"Loading plugin: {name} from {path}")

        registry = get_registry()
        old_module = self._loaded.get(name)
        old_handlers = registry.take_plugin(name)  # set aside; restored if the new version fails

        try:
            module = self._exec_module(name, path)
        except Exception as exc:
            registry.clear_plugin(name)
            registry.restore_plugin(old_handlers)
            if old_module is not None:
                sys.modules[_module_name(name)] = old_module
            self._mark_failed(name, path, exc)
            suffix = " (previous version still running)" if old_module is not None else ""
            raise PluginLoadError(f"Failed to load plugin '{name}': {exc}{suffix}") from exc

        # The new version executed cleanly: retire the old one, then start the new one
        if old_module is not None:
            await self._shutdown_module(name, old_module)

        self._loaded[name] = module
        self._paths[name] = path
        try:
            await self._call_hook(module, "setup")
        except Exception as exc:
            await self._drop(name)
            self._mark_failed(name, path, exc)
            raise PluginLoadError(f"Plugin '{name}' setup() failed: {exc}") from exc

        self._start_jobs(name)
        self._failed.pop(name, None)
        self._errors.pop(name, None)
        logger.info(f"Plugin loaded: {name}")

    async def unload(self, name: str) -> None:
        """Unload a plugin and remove all its registered handlers and jobs."""
        if name not in self._loaded:
            logger.warning(f"Plugin '{name}' is not loaded")
            return
        await self._shutdown_module(name, self._loaded[name])
        await self._drop(name)
        self._mtimes.pop(name, None)
        self._failed.pop(name, None)
        logger.info(f"Plugin unloaded: {name}")

    async def reload(self, name: str) -> None:
        """Reload a plugin by name. Raises PluginLoadError if the new version fails."""
        path = self._paths.get(name) or self._failed.get(name) or self._available_paths.get(name)
        if path is None:
            raise KeyError(f"Plugin '{name}' is not known (never loaded)")
        await self.load(name, path)

    async def reload_all(self) -> None:
        """Reload all currently loaded plugins; a failure in one does not stop the rest."""
        for name in list(self._loaded.keys()):
            try:
                await self.reload(name)
            except PluginLoadError as exc:
                logger.error(str(exc))

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
                previous = self._available_paths.get(name)
                if previous is not None and previous != path:
                    logger.warning(
                        f"Plugin '{name}' in {path.parent} replaces the one in {previous.parent}"
                    )
                self._available_paths[name] = path
                if name in disabled:
                    logger.debug(f"Skipping disabled plugin: {name}")
                    continue
                if enabled != "all" and name not in enabled:
                    logger.debug(f"Skipping non-enabled plugin: {name}")
                    continue
                try:
                    await self.load(name, path)
                except PluginLoadError as exc:
                    logger.error(str(exc))

        # Start the file watcher
        self._watch_task = asyncio.create_task(self._watch_for_changes(), name="plugin-watcher")

    def get_loaded_plugins(self) -> dict[str, Path]:
        return dict(self._paths)

    def get_available_plugins(self) -> dict[str, Path]:
        return dict(self._available_paths)

    def get_errors(self) -> dict[str, str]:
        """Last load error per plugin, for plugins whose most recent load failed."""
        return dict(self._errors)

    def get_module(self, name: str) -> "ModuleType | None":
        return self._loaded.get(name)

    def is_loaded(self, name: str) -> bool:
        return name in self._loaded

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _exec_module(self, name: str, path: Path) -> ModuleType:
        module_name = _module_name(name)
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create spec for {path}")
        module = importlib.util.module_from_spec(spec)

        # Invalidate import caches and drop stale bytecode to make rapid reloads reliable.
        importlib.invalidate_caches()
        pyc_path = Path(importlib.util.cache_from_source(str(path)))
        if pyc_path.exists():
            try:
                pyc_path.unlink()
            except PermissionError:
                # Site-packages may be read-only in containers running as non-root.
                logger.debug(f"Cannot remove bytecode cache (permission denied): {pyc_path}")

        _set_current_plugin(name)
        # Register in sys.modules so relative imports work.
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        finally:
            _set_current_plugin("unknown")
        return module

    async def _call_hook(self, module: ModuleType, hook: str) -> None:
        func = getattr(module, hook, None)
        if callable(func):
            result = func(self._bot)
            if inspect.isawaitable(result):
                await result

    async def _shutdown_module(self, name: str, module: ModuleType) -> None:
        self._stop_jobs(name)
        try:
            await self._call_hook(module, "shutdown")
        except Exception as exc:
            logger.error(f"Plugin '{name}' shutdown() error: {exc}")

    async def _drop(self, name: str) -> None:
        self._stop_jobs(name)
        get_registry().clear_plugin(name)
        sys.modules.pop(_module_name(name), None)
        self._loaded.pop(name, None)
        self._paths.pop(name, None)

    def _mark_failed(self, name: str, path: Path, exc: Exception) -> None:
        self._errors[name] = str(exc)
        if name not in self._loaded:
            self._failed[name] = path
        logger.error(f"Failed to load plugin '{name}': {exc}")

    def _record_mtime(self, name: str, path: Path) -> None:
        try:
            self._mtimes[name] = path.stat().st_mtime
        except OSError:
            self._mtimes.pop(name, None)

    def _scheduler(self) -> object | None:
        return getattr(self._bot, "scheduler", None)

    def _start_jobs(self, name: str) -> None:
        scheduler = self._scheduler()
        if scheduler is None:
            return
        for handler in get_registry().intervals:
            if handler.plugin_name == name:
                scheduler.add_interval_handler(handler)  # type: ignore[attr-defined]

    def _stop_jobs(self, name: str) -> None:
        scheduler = self._scheduler()
        if scheduler is not None:
            scheduler.remove_plugin_jobs(name)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------
    # Hot-reload file watcher
    # ------------------------------------------------------------------

    async def _watch_for_changes(self) -> None:
        """Poll file mtimes every 5 seconds; reload changed plugins and retry failed ones."""
        while True:
            await asyncio.sleep(5)
            watched = {**self._failed, **self._paths}
            for name, path in watched.items():
                try:
                    current_mtime = path.stat().st_mtime
                    if current_mtime == self._mtimes.get(name):
                        continue
                    logger.info(f"Plugin '{name}' changed on disk — reloading")
                    await self.load(name, path)
                except FileNotFoundError:
                    logger.warning(f"Plugin file missing: {path} — unloading '{name}'")
                    self._failed.pop(name, None)
                    await self.unload(name)
                except PluginLoadError as exc:
                    logger.error(str(exc))
                except Exception as exc:
                    logger.error(f"Watcher error for plugin '{name}': {exc}")
