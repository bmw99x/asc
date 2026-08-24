"""Application-wide constants."""

from asc.models import compose_kv_ref

APP_TITLE = "asc — Azure App Service Config"

HELP_TEXT = """\
 Navigation
 ──────────────────────────────
 j / ↓        Move down
 k / ↑        Move up
 g g          Jump to top
 G            Jump to bottom
 e / Tab      Next slot

 Edit
 ──────────────────────────────
 i / Enter    Edit selected value
 r            Rename selected key
 o            Add new setting
 d d          Delete selected setting
 t            Toggle deployment slot setting
 u            Undo last change
 s            Save changes

 Search
 ──────────────────────────────
 /            Open search
 Escape       Clear search / close

 General
 ──────────────────────────────
 y            Copy value (Key Vault rows copy raw reference)
 Y            Copy value (resolve Key Vault reference)
 p            Switch group / app
 ?            Toggle this help
 q            Quit\
"""

TABLE_COLUMNS = ("Key", "Value", "Badges")

PRODUCTION = "production"

DEFAULT_GROUP: str = "MyProduct"
DEFAULT_APP: str = "api"

# Mock group -> list of app aliases.
GROUPS: dict[str, list[str]] = {
    "MyProduct": ["api", "web"],
    "Internal": ["admin"],
}

# group -> app -> slot -> raw settings (as returned by `az webapp config appsettings list`).
MOCK_DATA: dict[str, dict[str, dict[str, list[dict]]]] = {
    "MyProduct": {
        "api": {
            "production": [
                {"name": "APP_ENV", "value": "production", "slotSetting": False},
                {
                    "name": "DATABASE_URL",
                    "value": compose_kv_ref("kv-myproduct-prod", "database-url"),
                    "slotSetting": True,
                },
                {"name": "LOG_LEVEL", "value": "info", "slotSetting": False},
                {"name": "NEW_TO_DELETE", "value": "stale", "slotSetting": False},
            ],
            "staging": [
                {"name": "APP_ENV", "value": "staging", "slotSetting": False},
                {
                    "name": "DATABASE_URL",
                    "value": compose_kv_ref("kv-myproduct-stg", "database-url"),
                    "slotSetting": True,
                },
                {"name": "LOG_LEVEL", "value": "debug", "slotSetting": False},
            ],
        },
        "web": {
            "production": [
                {"name": "APP_ENV", "value": "production", "slotSetting": False},
                {"name": "CDN_URL", "value": "https://cdn.example.com", "slotSetting": False},
            ],
            "staging": [
                {"name": "APP_ENV", "value": "staging", "slotSetting": False},
                {
                    "name": "CDN_URL",
                    "value": "https://staging-cdn.example.com",
                    "slotSetting": False,
                },
            ],
        },
    },
    "Internal": {
        "admin": {
            "production": [
                {"name": "APP_ENV", "value": "production", "slotSetting": False},
                {
                    "name": "SECRET_KEY",
                    "value": compose_kv_ref("kv-internal-prod", "secret-key"),
                    "slotSetting": True,
                },
            ],
            "staging": [
                {"name": "APP_ENV", "value": "staging", "slotSetting": False},
            ],
        },
    },
}
