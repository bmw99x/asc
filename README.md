# asc

A terminal UI for managing Azure App Service application settings.

![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue)

⚠️ **EXPERIMENTAL — USE AT YOUR OWN RISK**

This tool is experimental and provided as-is. The author is not responsible for any data loss, accidental deletion, misconfiguration, or other problems that may result from using this tool. Always ensure you have proper backups of your Azure App Service configuration and review all changes carefully before committing them to production apps.

## Features

- Browse, add, edit, rename, and delete app settings across groups, apps, and deployment slots
- **Staged changes** — all mutations are held locally until you explicitly save; nothing touches Azure until you confirm
- **Save confirm screen** — pressing `s` shows a coloured diff (added/removed/renamed/edited/sticky-toggled) before any write
- Undo stack for all staged mutations within a session
- **Slot tabs** — deployment slots are discovered live from Azure (not from config), with `production` always shown first
- Toggle a setting's "deploy to all slots" (sticky) flag, staged like any other change
- Key Vault reference detection — settings pointing at `@Microsoft.KeyVault(...)` are shown as `vault/secret` with a `[KV]` badge; copy the raw reference or resolve and copy the underlying secret value
- Context switcher to jump between groups and apps
- Search/filter across keys and values

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- Azure CLI (`az`) — authenticated via `az login` or a service principal

## Install

```bash
uv tool install git+https://github.com/bmw99x/asc
```

Or clone and install locally:

```bash
git clone https://github.com/bmw99x/asc
cd asc
uv tool install .
```

## Configuration

asc reads `~/.config/asc/config.json`. The schema maps group names to app aliases:

```json
{
  "MyProduct": {
    "api": {
      "app_name": "app-api",
      "resource_group": "rg-prod",
      "subscription_id": "00000000-0000-0000-0000-000000000000",
      "tenant_id": "00000000-0000-0000-0000-000000000001"
    },
    "web": {
      "app_name": "app-web",
      "resource_group": "rg-prod",
      "subscription_id": "00000000-0000-0000-0000-000000000000",
      "tenant_id": "00000000-0000-0000-0000-000000000001"
    }
  }
}
```

`app_name`, `resource_group` and `subscription_id` are required. `tenant_id` is optional and informational only — every `az` call is scoped by subscription, so asc never reads it.

Keys prefixed with `_` (e.g. `_example`) are ignored by asc.

A blank config file and a companion `README.md` describing the schema are created automatically on first run. If the config is empty or missing, asc falls back to a built-in mock dataset so you can try it without Azure credentials. If the config exists but is malformed — invalid JSON, or a binding missing a required field — asc prints the error and exits non-zero rather than showing mock data that looks real.

### Auto-configure from Azure

`asc-autoconfig` discovers all App Services across your subscriptions and writes the config for you:

```bash
asc-autoconfig ~/.config/asc/config.json
```

Optionally map resource group names to friendlier group names:

```bash
asc-autoconfig ~/.config/asc/config.json --service-name-mapping '{"rg-myapp": "MyApp"}'
```

Re-running `asc-autoconfig` merges by group name, but each rediscovered group **replaces the same-named group wholesale** — any app aliases you hand-added into that group are dropped. Put hand-added aliases in their own group, or re-add them after re-running autoconfig.

## Usage

```bash
asc
```

### Keybindings

| Key           | Action                                    |
| ------------- | ------------------------------------------ |
| `j` / `↓`     | Move down                                   |
| `k` / `↑`     | Move up                                     |
| `g g`         | Jump to top                                 |
| `G`           | Jump to bottom                              |
| `e` / `Tab`   | Next slot                                   |
| `S`           | Cycle sort (A-Z / Z-A / Azure order)        |
| `i` / `Enter` | Edit selected value                         |
| `r`           | Rename selected key                         |
| `o`           | Add new setting                             |
| `d d`         | Delete selected setting                     |
| `t`           | Toggle deployment slot setting (sticky)     |
| `u`           | Undo last change                            |
| `s`           | Save changes                                |
| `/`           | Open search                                 |
| `Escape`      | Clear search / close                        |
| `y`           | Copy value (Key Vault rows copy raw reference) |
| `Y`           | Copy value (resolve Key Vault reference)    |
| `p`           | Switch group / app                          |
| `?`           | Toggle help                                 |
| `q`           | Quit (prompts if changes are staged)        |

Clicking a slot tab or the app label in the tab bar performs the same navigation as `e`/`Tab` and `p`. Double-clicking a row also opens the edit modal.

### Staged changes

Edits, additions, renames, deletions, and sticky-flag toggles are all held in a local stage — nothing is written to Azure until you press `s`. The save screen shows a colour-coded diff of the write that is about to happen (derived from the settings as loaded, so changes that cancel each other out never appear):

- **Green** — new setting added
- **Red** — setting deleted
- **Yellow** — setting renamed
- **Blue** — setting value edited
- **Magenta** — deployment slot setting (sticky flag) toggled

Press `y` (or click Save) to commit all staged changes to Azure, or `n`/`Esc` (or click Cancel) to go back and keep editing. If any write fails the remaining staged changes are left intact so you can retry.

Pressing `u` reverses the most recent staged change without touching Azure. Pressing `q` with staged changes asks for confirmation before discarding them, and switching slot, app or group is refused while a save is still being written.

### Deployment slots

Slot tabs are populated by discovering the app's live deployment slots from Azure — they are not read from config. `production` is always listed first, followed by any other slots in sorted order. Switching slots (or apps, or groups) with unsaved changes prompts for confirmation before dropping the stage.

### Key Vault references

Settings whose value is an `@Microsoft.KeyVault(...)` reference are displayed as `vault/secret` in dim italic with a cyan `[KV]` badge; slot-sticky settings additionally get a yellow `[slot]` badge.

- `y` copies the raw `@Microsoft.KeyVault(...)` reference to the clipboard.
- `Y` resolves the reference against Key Vault and copies the underlying secret value instead.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check
uv run ty check src/ tests/
```
