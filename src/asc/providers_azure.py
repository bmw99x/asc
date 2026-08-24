"""Azure-backed settings provider."""

from typing import Protocol, cast

from asc.azure.client import AzureClient
from asc.config import AppBinding
from asc.constants import PRODUCTION
from asc.models import AppSetting, KeyVaultRef


class _Client(Protocol):
    """Structural shape of the client an AzureSettingsProvider talks to."""

    def list_slots(self) -> list[str]: ...

    def list_settings(self, slot: str | None = None) -> list[dict]: ...

    def set_settings(self, settings: list[dict], slot: str | None = None) -> None: ...

    def delete_settings(self, names: list[str], slot: str | None = None) -> None: ...


def _to_setting(raw: dict) -> AppSetting:
    return AppSetting(
        key=raw["name"], value=raw["value"], slot_setting=bool(raw.get("slotSetting", False))
    )


def _to_raw(setting: AppSetting) -> dict:
    return {"name": setting.key, "value": setting.value, "slotSetting": setting.slot_setting}


class AzureSettingsProvider:
    """Wraps AzureClient, translating the "production" slot name to None."""

    def __init__(self, binding: AppBinding) -> None:
        self._client: _Client = AzureClient(
            binding.app_name, binding.resource_group, binding.subscription_id
        )
        self._slots: list[str] | None = None

    def list_slots(self) -> list[str]:
        if self._slots is None:
            self._slots = [PRODUCTION, *sorted(self._client.list_slots())]
        return self._slots

    def list_settings(self, slot: str) -> list[AppSetting]:
        raws = self._client.list_settings(slot=self._az_slot(slot))
        return [_to_setting(raw) for raw in raws]

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        az_slot = self._az_slot(slot)
        if upserts:
            self._client.set_settings([_to_raw(s) for s in upserts], slot=az_slot)
        if deletes:
            self._client.delete_settings(deletes, slot=az_slot)

    def resolve_kv(self, ref: KeyVaultRef) -> str:
        return cast(AzureClient, self._client).resolve_kv_secret(ref.vault, ref.secret)

    def _az_slot(self, slot: str) -> str | None:
        return None if slot == PRODUCTION else slot
