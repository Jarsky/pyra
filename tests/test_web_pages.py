"""Render the web admin pages touched by plugin management and submit their forms."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from pybot.core.bot import ChannelState, PyraBot
from pybot.core.config import BotConfig
from pybot.core.database import User, close_db, get_session, init_db
from pybot.core.permissions import add_flag, add_owner_bootstrap
from pybot.core.plugin_loader import PluginLoader
from pybot.web.app import create_app
from pybot.web.auth import create_access_token, hash_password

SECRET = "testsecret-0123456789abcdef0123456789abcdef"

PLUGIN = """
__plugin_config__ = {
    "api_key": {"type": "str", "secret": True, "help": "Service key"},
    "units": {"type": "choice", "choices": ["metric", "imperial"], "default": "metric"},
    "limit": {"type": "int", "default": 5, "min": 1, "max": 10},
    "enabled": {"type": "bool", "default": True},
    "domains": {"type": "list"},
}
from pybot import plugin

@plugin.command("wx")
async def wx(bot, trigger):
    pass
"""


@pytest.fixture
async def client(tmp_path: Path, minimal_config_dict: dict, monkeypatch: Any) -> Any:
    await init_db("sqlite+aiosqlite:///:memory:")
    async with get_session() as session:
        await add_owner_bootstrap(session, "boss", "*!boss@home", hash_password("pw12345678"))
        session.add(User(nick="helper", hostmask="*!h@h", password_hash=hash_password("x")))
    async with get_session() as session:
        await add_flag(session, {"n"}, "*!h@h", "a")  # an admin, not an owner

    config_path = tmp_path / "config.yaml"
    cfg = dict(minimal_config_dict)
    cfg["plugins"] = {**cfg["plugins"], "enabled": "all", "vars": {"wxplug": {"api_key": "sekrit"}}}
    config_path.write_text(yaml.safe_dump(cfg))
    monkeypatch.setenv("CONFIG_FILE", str(config_path))

    bot = PyraBot(BotConfig.model_validate(cfg))
    bot.channels["#test"] = ChannelState(name="#test")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "wxplug.py").write_text(PLUGIN)
    bot.plugin_loader = PluginLoader(bot)
    await bot.plugin_loader.load_all([plugin_dir])

    with TestClient(create_app(bot), base_url="http://testserver") as test_client:
        test_client.cookies.set(
            "access_token", create_access_token(SECRET, "boss", timedelta(minutes=5))
        )
        test_client.bot = bot  # type: ignore[attr-defined]
        test_client.config_path = config_path  # type: ignore[attr-defined]
        yield test_client

    if bot.plugin_loader._watch_task:
        bot.plugin_loader._watch_task.cancel()
    await bot.plugin_loader.unload("wxplug")
    await close_db()


def test_plugin_pages_render_settings_form_without_secrets(client: TestClient) -> None:
    listing = client.get("/plugins/")
    assert listing.status_code == 200 and "wxplug" in listing.text

    detail = client.get("/plugins/wxplug")
    assert detail.status_code == 200
    assert 'action="/plugins/wxplug/save-settings"' in detail.text
    assert 'type="password" name="api_key"' in detail.text
    assert "Saved - leave blank to keep" in detail.text
    page_without_editor = detail.text.split("Edit Config Vars (YAML)")[0]
    assert "sekrit" not in page_without_editor  # masked in the vars card and the form


def test_save_settings_form(client: TestClient) -> None:
    response = client.post(
        "/plugins/wxplug/save-settings",
        data={"api_key": "", "units": "imperial", "limit": "7", "domains": "a.com\nb.com"},
        follow_redirects=False,
    )
    assert response.status_code == 303 and "success" in response.headers["location"]
    saved = yaml.safe_load(client.config_path.read_text())["plugins"]["vars"]["wxplug"]  # type: ignore[attr-defined]
    assert saved == {
        "api_key": "sekrit",
        "units": "imperial",
        "limit": 7,
        "enabled": False,
        "domains": ["a.com", "b.com"],
    }

    bad = client.post("/plugins/wxplug/save-settings", data={"units": "x"}, follow_redirects=False)
    assert "error" in bad.headers["location"]


def test_channel_settings_plugin_switches(client: TestClient) -> None:
    page = client.get("/channels/#test/settings".replace("#", "%23"))
    assert page.status_code == 200
    assert 'name="plugin_wxplug" checked' in page.text
    assert 'name="autoop"' in page.text

    response = client.post("/channels/%23test/plugins", data={}, follow_redirects=False)
    assert response.status_code == 303
    assert ("wxplug", "#test") in client.bot.disabled_plugin_channels  # type: ignore[attr-defined]


def test_non_owner_admins_cannot_change_plugins(client: TestClient) -> None:
    client.cookies.set("access_token", create_access_token(SECRET, "helper", timedelta(minutes=5)))
    page = client.get("/plugins/wxplug")
    assert page.status_code == 200 and "save-settings" not in page.text  # no edit forms

    response = client.post(
        "/plugins/wxplug/save-settings", data={"units": "metric"}, follow_redirects=False
    )
    assert "Owner+flag+required" in response.headers["location"]
    unload = client.post("/plugins/wxplug/unload", follow_redirects=False)
    assert "Owner+flag+required" in unload.headers["location"]
    assert client.bot.plugin_loader.is_loaded("wxplug")  # type: ignore[attr-defined]


def test_users_page_hosts_and_audit_page(client: TestClient) -> None:
    page = client.get("/users/")
    assert page.status_code == 200
    assert 'placeholder="*!ident@host"' in page.text and "*!boss@home" in page.text

    import re

    helper_id = re.search(
        r'action="/users/(\d+)/hosts"[^>]*>\s*<input type="hidden" name="action" value="add">',
        page.text,
    )
    assert helper_id
    user_id = helper_id.group(1)
    added = client.post(
        f"/users/{user_id}/hosts",
        data={"action": "add", "mask": "*!second@laptop.example"},
        follow_redirects=False,
    )
    assert "success" in added.headers["location"]
    flagged = client.post(
        f"/users/{user_id}/flags",
        data={"action": "add", "flag": "v", "channel": ""},
        follow_redirects=False,
    )
    assert "success" in flagged.headers["location"]

    audit = client.get("/logs/audit")
    assert audit.status_code == 200
    assert "host.add" in audit.text and "*!second@laptop.example" in audit.text
    assert "flag.grant" in audit.text
    assert client.get("/logs/audit?action=host.").text.count("<code>host.add</code>") == 1

    logs = client.get("/logs/")
    assert "kept for 30 days" in logs.text
