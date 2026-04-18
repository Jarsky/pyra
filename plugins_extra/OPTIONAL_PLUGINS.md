# Pyra Plugin Staging

This folder is a staging area for plugin conversions from the temporary `eggdrop-scripts/` import.

Current intent:

- prototype converted plugins before deciding where they belong
- separate bundled-core candidates from optional extra plugins
- keep first-party custom conversions separate from direct Eggdrop-core ports

Notes:

- The current bot does not automatically load plugins from this folder.
- Built-in plugins belong in `pybot/plugins/`.
- Optional runtime plugins currently belong in `plugins_extra/`.
- This folder exists to keep migration work organized while we classify and port functionality.

See [docs/eggdrop-migration-plan.md](../docs/eggdrop-migration-plan.md) for the current conversion inventory.