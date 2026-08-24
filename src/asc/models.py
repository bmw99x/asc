"""Domain models."""

import re
from dataclasses import dataclass
from enum import Enum, auto

_KV_PREFIX = re.compile(r"@microsoft\.keyvault\((?P<body>.*)\)\s*$", re.IGNORECASE)
_URI = re.compile(
    r"SecretUri=https://(?P<vault>[^.]+)\.vault\.azure\.net/secrets/(?P<secret>[^/;)]+)",
    re.IGNORECASE,
)
_NAMED = re.compile(
    r"VaultName=(?P<vault>[^;)]+);SecretName=(?P<secret>[^;)]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class KeyVaultRef:
    """Reference to a secret in Azure Key Vault."""

    vault: str
    secret: str
    raw: str


def is_kv_ref(value: str) -> bool:
    """Return True if value starts with @Microsoft.KeyVault( (case-insensitive)."""
    return value.lstrip().lower().startswith("@microsoft.keyvault(")


def parse_kv_ref(value: str) -> KeyVaultRef | None:
    """Parse a Key Vault reference from value.

    Supports two formats:
    - SecretUri: @Microsoft.KeyVault(SecretUri=https://{vault}.vault.azure.net/secrets/{secret})
    - VaultName: @Microsoft.KeyVault(VaultName={vault};SecretName={secret})

    Returns None if the value is not a Key Vault reference or is malformed.
    """
    m = _KV_PREFIX.search(value.strip())
    if not m:
        return None
    body = m.group("body")
    for pat in (_URI, _NAMED):
        hit = pat.search(body)
        if hit:
            return KeyVaultRef(
                vault=hit.group("vault"), secret=hit.group("secret"), raw=value
            )
    return None


def compose_kv_ref(vault: str, secret: str) -> str:
    """Compose a Key Vault reference in SecretUri format."""
    return f"@Microsoft.KeyVault(SecretUri=https://{vault}.vault.azure.net/secrets/{secret})"


@dataclass
class AppSetting:
    """An Azure App Service configuration setting.

    Supports Key Vault references via the kv_ref property.
    """

    key: str
    value: str
    slot_setting: bool = False

    def matches(self, query: str) -> bool:
        """Return True if key or value contains the query (case-insensitive)."""
        q = query.lower()
        return q in self.key.lower() or q in self.value.lower()

    @property
    def kv_ref(self) -> "KeyVaultRef | None":
        """Parse and return the Key Vault reference, if present."""
        return parse_kv_ref(self.value)


class ActionKind(Enum):
    """Types of reversible mutations."""

    SET = auto()
    DELETE = auto()
    RENAME = auto()
    TOGGLE_STICKY = auto()


@dataclass
class Action:
    """A reversible mutation applied to the setting set.

    Used to build an undo stack. Each action records enough information to
    reverse itself:
    - SET (add or edit): reverse by restoring previous_value and
      previous_slot_setting (or deleting if there was no previous value, i.e.
      it was an add).
    - DELETE: reverse by re-inserting the deleted key/value/slot_setting.
    - RENAME: reverse by renaming key back to old_key.
    - TOGGLE_STICKY: reverse by toggling slot_setting again (self-inverse).
    """

    kind: ActionKind
    key: str
    value: str
    previous_value: str | None = None
    old_key: str | None = None
    slot_setting: bool = False
    previous_slot_setting: bool = False
