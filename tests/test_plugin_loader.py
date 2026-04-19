"""Tests for the plugin loader (load, unload, reload)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pybot.core.plugin_loader import PluginLoader
from pybot.plugin import get_registry


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.config = MagicMock()
    bot.config.plugins.enabled = "all"
    bot.config.plugins.disabled = []
    return bot


@pytest.fixture
def plugin_dir(tmp_path: Path) -> Path:
    return tmp_path / "plugins"


def _write_plugin(plugin_dir: Path, name: str, content: str) -> Path:
    plugin_dir.mkdir(exist_ok=True)
    path = plugin_dir / f"{name}.py"
    path.write_text(content)
    return path


async def test_load_simple_plugin(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "testplugin",
        """
from pybot import plugin

@plugin.command("testcmd")
async def cmd(bot, trigger):
    pass
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("testplugin", path)

    assert loader.is_loaded("testplugin")
    registry = get_registry()
    assert "testcmd" in registry.commands
    handlers = registry.commands["testcmd"]
    assert any(h.plugin_name == "testplugin" for h in handlers)

    # Cleanup
    await loader.unload("testplugin")
    get_registry().clear_plugin("testplugin")


async def test_unload_removes_commands(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "unloadtest",
        """
from pybot import plugin

@plugin.command("unloadcmd")
async def cmd(bot, trigger):
    pass
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("unloadtest", path)
    assert "unloadcmd" in get_registry().commands

    await loader.unload("unloadtest")
    assert not loader.is_loaded("unloadtest")
    assert "unloadcmd" not in get_registry().commands


async def test_reload_plugin(mock_bot: MagicMock, plugin_dir: Path, tmp_path: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "reloadtest",
        """
from pybot import plugin

@plugin.command("v1cmd")
async def cmd(bot, trigger):
    pass
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("reloadtest", path)
    assert "v1cmd" in get_registry().commands

    # Rewrite the plugin
    path.write_text("""
from pybot import plugin

@plugin.command("v2cmd")
async def cmd(bot, trigger):
    pass
""")
    await loader.reload("reloadtest")

    assert "v2cmd" in get_registry().commands
    assert "v1cmd" not in get_registry().commands

    await loader.unload("reloadtest")


async def test_load_invalid_plugin_does_not_crash(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "badplugin",
        "raise RuntimeError('intentional load error')\n",
    )
    loader = PluginLoader(mock_bot)
    # Should log an error but not raise
    await loader.load("badplugin", path)
    assert not loader.is_loaded("badplugin")


async def test_plugin_setup_called(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "setupplugin",
        """
setup_called = False

async def setup(bot):
    global setup_called
    setup_called = True
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("setupplugin", path)
    import sys

    mod = sys.modules.get("pybot.plugins._loaded.setupplugin")
    assert mod is not None
    assert mod.setup_called is True

    await loader.unload("setupplugin")


async def test_plugin_shutdown_called(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "shutdownplugin",
        """
shutdown_called = False

async def shutdown(bot):
    global shutdown_called
    shutdown_called = True
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("shutdownplugin", path)
    import sys

    mod = sys.modules.get("pybot.plugins._loaded.shutdownplugin")
    await loader.unload("shutdownplugin")
    assert mod is not None
    assert mod.shutdown_called is True


async def test_plugin_sync_setup_called(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "syncsetupplugin",
        """
setup_called = False

def setup(bot):
    global setup_called
    setup_called = True
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("syncsetupplugin", path)
    import sys

    mod = sys.modules.get("pybot.plugins._loaded.syncsetupplugin")
    assert mod is not None
    assert mod.setup_called is True

    await loader.unload("syncsetupplugin")


async def test_plugin_sync_shutdown_called(mock_bot: MagicMock, plugin_dir: Path) -> None:
    path = _write_plugin(
        plugin_dir,
        "syncshutdownplugin",
        """
shutdown_called = False

def shutdown(bot):
    global shutdown_called
    shutdown_called = True
""",
    )
    loader = PluginLoader(mock_bot)
    await loader.load("syncshutdownplugin", path)
    import sys

    mod = sys.modules.get("pybot.plugins._loaded.syncshutdownplugin")
    await loader.unload("syncshutdownplugin")
    assert mod is not None
    assert mod.shutdown_called is True


async def test_load_all_from_directory(mock_bot: MagicMock, plugin_dir: Path) -> None:
    _write_plugin(
        plugin_dir,
        "pa",
        "from pybot import plugin\n@plugin.command('pa')\nasync def f(b,t): pass\n",
    )
    _write_plugin(
        plugin_dir,
        "pb",
        "from pybot import plugin\n@plugin.command('pb')\nasync def f(b,t): pass\n",
    )
    _write_plugin(plugin_dir, "_private", "# should be skipped\n")

    loader = PluginLoader(mock_bot)
    await loader.load_all([plugin_dir])

    assert loader.is_loaded("pa")
    assert loader.is_loaded("pb")
    assert not loader.is_loaded("_private")

    for name in list(loader.get_loaded_plugins()):
        await loader.unload(name)
