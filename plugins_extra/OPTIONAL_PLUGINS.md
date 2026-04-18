# Optional Plugins (`plugins_extra`)

This directory contains first-party optional plugins that are loaded at runtime
via `plugins.extra_dir` (default: `/plugins_extra` in Docker).

How this directory is used:

- keep non-core or integration-heavy plugins separate from built-in plugins
- allow easy add/remove without touching the core plugin package
- keep per-plugin configuration under `plugins.vars.<plugin_name>` in `config.yaml`

Runtime behavior:

- plugins in this directory are discovered automatically when `extra_dir` exists
- they appear in the Web UI Plugins page with source labeled as `Extra`
- they can be loaded, unloaded, and reloaded from the Web UI

Current optional plugins include:

- `headlines`, `weather`, `url`
- `arrnotify`, `invite`, `ipinfo`, `lastfm`, `movies`
- `remind`, `selfauth`, `timebot`, `translate`, `tvmaze`, `voting`

Notes for maintainers:

- keep plugin metadata (`__plugin_meta__`) updated so Web UI details stay accurate
- prefer documenting plugin vars in each plugin docstring
- if a plugin becomes universally useful and low-risk, it can be promoted to `pybot/plugins/`