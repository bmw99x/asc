"""Settings provider protocol and mock implementation."""

import copy
from typing import Protocol

from asc.constants import DEFAULT_APP, DEFAULT_GROUP, MOCK_DATA, PRODUCTION
from asc.models import AppSetting, KeyVaultRef


class SettingsProvider(Protocol):
    """Protocol that all settings backends must satisfy.

    Slot name convention: the string "production" means production;
    implementations translate it to whatever their backend expects.
    """

    def list_slots(self) -> list[str]:
        """Return slot display names, with "production" first."""
        ...

    def list_settings(self, slot: str) -> list[AppSetting]:
        """Return all settings for the given slot."""
        ...

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        """Upsert and delete settings for the given slot."""
        ...

    def resolve_kv(self, ref: KeyVaultRef) -> str:
        """Resolve a Key Vault reference to its secret value."""
        ...


class MockProvider:
    """In-memory provider seeded from MOCK_DATA for the given group and app."""

    def __init__(self, group: str = DEFAULT_GROUP, app: str = DEFAULT_APP) -> None:
        self._data: dict[str, list[dict]] = copy.deepcopy(MOCK_DATA.get(group, {}).get(app, {}))

    def list_slots(self) -> list[str]:
        slots = [s for s in self._data if s != PRODUCTION]
        return [PRODUCTION, *sorted(slots)] if PRODUCTION in self._data else sorted(slots)

    def list_settings(self, slot: str) -> list[AppSetting]:
        return [AppSetting.from_raw(raw) for raw in self._data.get(slot, [])]

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        raws = self._data.setdefault(slot, [])
        by_key = {raw["name"]: raw for raw in raws}
        for setting in upserts:
            by_key[setting.key] = setting.to_raw()
        for key in deletes:
            by_key.pop(key, None)
        self._data[slot] = list(by_key.values())

    def resolve_kv(self, ref: KeyVaultRef) -> str:
        return f"mock-secret-value::{ref.vault}/{ref.secret}"
