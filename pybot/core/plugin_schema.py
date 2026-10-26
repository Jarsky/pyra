"""
Typed settings for plugins (``__plugin_config__``), rendered as a form in the web UI.

A plugin declares the settings it reads from ``bot.plugin_config(name)``:

    __plugin_config__ = {
        "api_key": {"type": "str", "secret": True, "help": "Last.fm API key"},
        "units": {"type": "choice", "choices": ["metric", "imperial"], "default": "metric"},
        "max_results": {"type": "int", "default": 5, "min": 1, "max": 20},
        "ignore_domains": {"type": "list", "help": "One per line"},
        "enabled": {"type": "bool", "default": True},
    }

Values are still stored in config.yaml under plugins.vars.<plugin>; anything not in the
schema (e.g. nested mappings) stays editable as YAML.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import ModuleType
from typing import Any

FIELD_TYPES = ("str", "int", "float", "bool", "list", "choice")
_SECRET_NAME = re.compile(r"(key|token|secret|password|passwd|dsn)", re.IGNORECASE)
MASK = "********"


def plugin_schema(module: ModuleType | None) -> dict[str, dict[str, Any]]:
    """The plugin's declared settings, keeping only well-formed field definitions."""
    raw = getattr(module, "__plugin_config__", None) if module else None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(name): dict(spec)
        for name, spec in raw.items()
        if isinstance(spec, Mapping) and spec.get("type", "str") in FIELD_TYPES
    }


def is_secret(name: str, spec: Mapping[str, Any] | None = None) -> bool:
    if spec is not None and "secret" in spec:
        return bool(spec["secret"])
    return bool(_SECRET_NAME.search(name))


def masked(values: Mapping[str, Any], schema: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """A copy safe to display: secret values replaced by a mask."""
    return {
        k: (MASK if v not in (None, "") and is_secret(k, schema.get(k)) else v)
        for k, v in values.items()
    }


def form_fields(
    schema: Mapping[str, Mapping[str, Any]], current: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Field descriptions for the template (secrets are never sent back to the browser)."""
    fields = []
    for name, spec in schema.items():
        kind = spec.get("type", "str")
        value = current.get(name, spec.get("default", [] if kind == "list" else ""))
        secret = is_secret(name, spec)
        fields.append(
            {
                "name": name,
                "type": kind,
                "help": spec.get("help", ""),
                "choices": list(spec.get("choices", [])),
                "min": spec.get("min"),
                "max": spec.get("max"),
                "secret": secret,
                "is_set": bool(current.get(name)),
                "value": (
                    "" if secret else ("\n".join(map(str, value)) if kind == "list" else value)
                ),
            }
        )
    return fields


def parse_form(
    schema: Mapping[str, Mapping[str, Any]], form: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate submitted values against the schema; returns the new settings for those keys.

    Raises ValueError listing every invalid field. A blank secret keeps its current value;
    a blank number falls back to the default (the key is removed).
    """
    result: dict[str, Any] = {}
    errors: list[str] = []
    for name, spec in schema.items():
        kind = spec.get("type", "str")
        raw = form.get(name)
        text = "" if raw is None else str(raw).strip()
        try:
            if kind == "bool":
                result[name] = raw is not None and text.lower() not in ("", "false", "0", "off")
            elif kind == "list":
                result[name] = [item.strip() for item in re.split(r"[\n,]", text) if item.strip()]
            elif is_secret(name, spec) and text == "":
                if name in current:
                    result[name] = current[name]
            elif kind in ("int", "float"):
                if text == "":
                    result[name] = None  # use the default
                    continue
                number = int(text) if kind == "int" else float(text)
                if spec.get("min") is not None and number < spec["min"]:
                    raise ValueError(f"must be at least {spec['min']}")
                if spec.get("max") is not None and number > spec["max"]:
                    raise ValueError(f"must be at most {spec['max']}")
                result[name] = number
            elif kind == "choice":
                if text not in spec.get("choices", []):
                    raise ValueError(f"must be one of {', '.join(spec.get('choices', []))}")
                result[name] = text
            else:
                result[name] = text
        except ValueError as exc:
            message = str(exc)
            if message.startswith("invalid literal") or message.startswith("could not convert"):
                message = f"must be a {'whole ' if kind == 'int' else ''}number"
            errors.append(f"{name} {message}")
    if errors:
        raise ValueError("; ".join(errors))
    return result


def merge_settings(current: Mapping[str, Any], updates: Mapping[str, Any]) -> dict[str, Any]:
    """Apply parse_form output: None removes a key (back to its default)."""
    merged = dict(current)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged
