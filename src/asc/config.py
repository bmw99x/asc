"""Config file loading, validation, and persistence.

Schema on disk (~/.config/asc/config.json):

    {
        "MyProduct": {
            "api": {
                "app_name": "app-api",
                "resource_group": "rg-prod",
                "subscription_id": "00000000-0000-0000-0000-000000000000",
                "tenant_id": "00000000-0000-0000-0000-000000000001"
            }
        }
    }

``tenant_id`` is optional and informational — asc scopes every call by
subscription.

Keys prefixed with "_" are reserved (e.g. "_example") and are stripped on load.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

CONFIG_PATH = Path("~/.config/asc/config.json").expanduser()

_README_PATH = Path("~/.config/asc/README.md").expanduser()

_README_CONTENT = """\
# asc configuration

Edit `config.json` in this directory to register your Azure App Service bindings.

## Schema

```json
{
    "<group-name>": {
        "<app-alias>": {
            "app_name": "my-app-service",
            "resource_group": "rg-prod",
            "subscription_id": "00000000-0000-0000-0000-000000000000",
            "tenant_id": "00000000-0000-0000-0000-000000000001"
        }
    }
}
```

`app_name`, `resource_group` and `subscription_id` are required. `tenant_id`
is optional and informational only — asc scopes every `az` call by
subscription, so you can leave it out.

## Example

```json
{
    "MyProduct": {
        "api": {
            "app_name": "app-api",
            "resource_group": "rg-prod",
            "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            "tenant_id": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
        },
        "web": {
            "app_name": "app-web",
            "resource_group": "rg-prod",
            "subscription_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
            "tenant_id": "yyyyyyyy-yyyy-yyyy-yyyy-yyyyyyyyyyyy"
        }
    }
}
```

Keys prefixed with `_` (e.g. `_example`) are ignored by asc.
"""


class AppBinding(BaseModel):
    """A single Azure App Service binding.

    ``tenant_id`` is optional and informational only: every ``az`` call is
    scoped by ``--subscription``, so asc never needs the tenant. autoconfig
    still records it because it is useful when debugging which directory a
    subscription lives in.
    """

    app_name: str
    resource_group: str
    subscription_id: str
    tenant_id: str | None = None


# group -> app alias -> AppBinding
Config = dict[str, dict[str, AppBinding]]


class ConfigError(Exception):
    """Raised when config.json exists but cannot be parsed or validated."""


def load_config() -> Config:
    """Load and validate the config file.

    Creates the config directory, an empty config.json, and a README on first
    run.  Returns an empty dict if the file is empty or contains no real
    entries.  Raises ConfigError if the file exists but is malformed.
    """
    if not CONFIG_PATH.exists():
        _bootstrap()
        return {}

    try:
        raw: object = json.loads(CONFIG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json is not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("config.json must be a JSON object at the top level")

    # Strip reserved/comment keys.
    groups = {k: v for k, v in raw.items() if not k.startswith("_")}

    config: Config = {}
    for group, apps in groups.items():
        if not isinstance(apps, dict):
            raise ConfigError(f"Group '{group}' must be a JSON object")
        config[group] = {}
        for app_alias, app_data in apps.items():
            if app_alias.startswith("_"):
                continue
            try:
                config[group][app_alias] = AppBinding.model_validate(app_data)
            except ValidationError as exc:
                raise ConfigError(
                    f"Invalid config for {group}/{app_alias}: {exc}"
                ) from exc

    return config


def save_config(config: Config) -> None:
    """Persist config to disk, creating directories as needed."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        group: {
            app_alias: app_binding.model_dump()
            for app_alias, app_binding in apps.items()
        }
        for group, apps in config.items()
    }
    CONFIG_PATH.write_text(json.dumps(payload, indent=2))


def _bootstrap() -> None:
    """Create the config directory, an empty config.json, and a README."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text("{}\n")
    if not _README_PATH.exists():
        _README_PATH.write_text(_README_CONTENT)


# Theme persistence
THEME_CONFIG_PATH = Path("~/.config/asc/theme.json").expanduser()


def load_theme() -> str | None:
    """Load the saved theme preference.

    Returns the theme name if set, None otherwise.
    """
    if not THEME_CONFIG_PATH.exists():
        return None
    try:
        data = json.loads(THEME_CONFIG_PATH.read_text())
        return data.get("theme")
    except (json.JSONDecodeError, AttributeError):
        return None


def save_theme(theme: str) -> None:
    """Save the theme preference to disk."""
    THEME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    THEME_CONFIG_PATH.write_text(json.dumps({"theme": theme}, indent=2))
