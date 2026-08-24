# asc — Azure App Service Config TUI: Design

Date: 2026-08-24
Status: Approved

## Purpose

A terminal UI for managing Azure App Service application settings (environment
variables), modelled on `kvt` (keyvault_tui). Browse app services in
user-defined groupings, view/edit settings per deployment slot, manage the
"deployment slot setting" (sticky) flag, and work with Key Vault reference
settings — everything the Azure portal's Environment Variables blade offers for
app settings.

Out of scope: connection strings, general site config (always-on, stack
settings), creating/deleting/swapping slots.

## Architecture

Standalone repo at `~/Development/asc`, package `asc`, console script `asc`.
Python 3.13+, uv, hatchling. Dependencies: `textual`, `pydantic`, `typer`
(same stack as kvt). Azure access exclusively via the `az` CLI (subprocess) —
no Azure SDK. Auth is the user's `az login` session.

Layers (mirrors kvt):

- `asc/config.py` — config load/validate/persist (pydantic)
- `asc/models.py` — domain models and staged-action types
- `asc/azure/client.py` — az CLI wrapper
- `asc/providers.py` — `SettingsProvider` protocol + `MockProvider`
- `asc/providers_azure.py` — Azure-backed provider
- `asc/app.py` — Textual app, staged-changes state, undo stack
- `asc/widgets/`, `asc/screens/` — table, tabs, modals (edit/add/rename/
  confirm/save-confirm/context-picker/help)
- `asc/tools/autoconfig/` — `asc-autoconfig` discovery CLI

## Config

`~/.config/asc/config.json`, two levels: group → app alias → binding.

```json
{
  "MyProduct": {
    "api": {
      "app_name": "app-myproduct-api",
      "resource_group": "rg-myproduct",
      "subscription_id": "00000000-0000-0000-0000-000000000000",
      "tenant_id": "00000000-0000-0000-0000-000000000001"
    }
  }
}
```

- Keys prefixed `_` are ignored (comments/examples), as in kvt.
- Bootstrap on first run: empty `config.json` + README in `~/.config/asc/`.
- Theme persistence in `~/.config/asc/theme.json` (kvt pattern).
- Slots are NOT in config — discovered live per app via
  `az webapp deployment slot list`. The production slot is always present and
  listed first; discovered slots follow alphabetically.

`asc-autoconfig <path>` discovers all webapps across accessible subscriptions
(`az webapp list`) and writes config grouped by resource group, with
`--service-name-mapping '{"rg-x": "Friendly"}'` to rename groups.

## Domain model

```python
@dataclass
class AppSetting:
    key: str
    value: str
    slot_setting: bool = False   # "deployment slot setting" sticky flag

@dataclass
class KeyVaultRef:
    vault: str        # vault name parsed from URI or VaultName=
    secret: str       # secret name
    raw: str          # original reference string
```

A setting is a KV reference when its value matches
`@Microsoft.KeyVault(...)` — both `SecretUri=https://<vault>.vault.azure.net/secrets/<name>[/<version>]`
and `VaultName=<vault>;SecretName=<name>[;SecretVersion=<v>]` forms are parsed.
Parsing is a pure function in `models.py`; unparseable references are still
badged `[KV]` with the raw value shown.

Staged actions (undo stack, kvt pattern):

- `SET` — add or edit (records previous value + previous sticky flag)
- `DELETE` — records deleted setting for reversal
- `RENAME` — records old key
- `TOGGLE_STICKY` — flips `slot_setting` (self-inverse)

Nothing touches Azure until save. Stage is per app+slot context; switching
context with unsaved changes prompts (kvt behavior).

## Azure client (`az` wrapper)

All commands include `--subscription`. Non-zero exit → `AzureClientError`
carrying stderr.

- List slots: `az webapp deployment slot list -n <app> -g <rg> --query "[].name"`
- List settings: `az webapp config appsettings list -n <app> -g <rg> [--slot <s>]`
  → `[{name, value, slotSetting}]`. Values arrive in the list call — no lazy
  hybrid loading needed (unlike Key Vault).
- Save (per slot, batched):
  1. Upserts + sticky flags in ONE call:
     `az webapp config appsettings set -n <app> -g <rg> [--slot <s>] --settings @<tmp.json>`
     where the temp file is `[{"name": ..., "value": ..., "slotSetting": bool}, ...]`
     containing only changed settings. Temp file written 0600 and deleted in
     `finally` (holds secrets).
  2. Deletions: `az webapp config appsettings delete --setting-names <k>...`
- Rename = delete old + set new (portal-equivalent; no native rename).
- KV secret resolution (for copy-resolved): `az keyvault secret show
  --vault-name <v> --name <s> --query value -o tsv`.

Save order: set before delete, EXCEPT renames where delete of the old key runs
with the deletions batch — a failed step aborts remaining steps and keeps the
whole stage intact for retry.

## UI

kvt keybindings preserved:

| Key | Action |
|---|---|
| `j`/`k`, `gg`, `G` | navigate (wrap) |
| `i`/`Enter` | edit setting |
| `r` | rename |
| `o` | add |
| `dd` | stage delete |
| `t` | toggle deployment-slot-setting flag (staged) |
| `y` | copy value (KV rows: copies raw reference) |
| `Y` | KV rows: resolve secret via az and copy actual value |
| `u` | undo |
| `s` | save-confirm screen |
| `/`, `Escape` | search / clear |
| `e`/`Tab` | next slot |
| `p` | group/app picker |
| `?` | help |
| `q` | quit |

- Main view: settings table for selected group/app/slot. Columns: key, value,
  badges. Badges: `[KV]` (KV reference — value column shows `vault/secret`),
  `[slot]` (sticky).
- Slot tabs across the top (kvt env-tabs widget adapted): `production` first,
  then discovered slots.
- Context picker screen: group → app (kvt context_picker adapted).
- Edit/Add modal: name, value, "deployment slot setting" checkbox, and a
  "Key Vault reference" mode toggle — when on, inputs become vault name +
  secret name and the stored value is composed as
  `@Microsoft.KeyVault(SecretUri=https://<vault>.vault.azure.net/secrets/<secret>)`.
- Save-confirm screen: colour diff — green add, red delete, yellow rename,
  blue edit, magenta sticky toggle. `y` commits, `n`/`Esc` returns.

## Error handling

- `az` failure → Textual notification with command + stderr excerpt; stage
  preserved.
- Config malformed → `ConfigError` with path/reason, app exits with message.
- Slot discovery failure → fall back to production-only with a warning
  notification.
- `Y` on non-KV row → no-op notification.

## Testing

- pytest + pytest-asyncio, textual Pilot for UI flows (kvt test layout).
- `MockProvider` seeded with fixtures incl. KV-ref values and sticky flags.
- Unit tests: KV-ref parsing (both forms + version suffix + malformed),
  action undo semantics incl. TOGGLE_STICKY, save batching (JSON payload
  contents, set/delete ordering), config validation.
- Azure client tested with subprocess mocked.
- Gates: `uv run pytest`, `uv run ruff check`, `uv run ty check src/ tests/`.
