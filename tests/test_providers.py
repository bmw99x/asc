"""Tests for settings providers (mock + azure)."""

from asc.constants import PRODUCTION
from asc.models import AppSetting
from asc.providers import MockProvider
from asc.providers_azure import AzureSettingsProvider


def test_mock_lists_production_first():
    p = MockProvider()
    slots = p.list_slots()
    assert slots[0] == PRODUCTION and "staging" in slots


def test_mock_settings_typed():
    p = MockProvider()
    settings = p.list_settings(PRODUCTION)
    assert all(isinstance(s, AppSetting) for s in settings)
    assert any(s.slot_setting for s in settings)
    assert any(s.kv_ref for s in settings)


def test_mock_apply_upsert_and_delete():
    p = MockProvider()
    p.apply(PRODUCTION, [AppSetting("NEW", "1")], ["NEW_TO_DELETE"])
    keys = {s.key for s in p.list_settings(PRODUCTION)}
    assert "NEW" in keys and "NEW_TO_DELETE" not in keys


class FakeClient:
    def __init__(self):
        self.calls = []

    def list_slots(self):
        return ["staging"]

    def list_settings(self, slot=None):
        return [{"name": "K", "value": "v", "slotSetting": True}]

    def set_settings(self, settings, slot=None):
        self.calls.append(("set", settings, slot))

    def delete_settings(self, names, slot=None):
        self.calls.append(("del", names, slot))


def test_azure_provider_translates_production_to_none():
    p = AzureSettingsProvider.__new__(AzureSettingsProvider)
    p._client = FakeClient()
    p._slots = None
    p.apply(PRODUCTION, [AppSetting("K", "v", slot_setting=True)], ["OLD"])
    assert p._client.calls[0] == ("set", [{"name": "K", "value": "v", "slotSetting": True}], None)
    assert p._client.calls[1] == ("del", ["OLD"], None)


def test_azure_provider_slot_passthrough_and_parse():
    p = AzureSettingsProvider.__new__(AzureSettingsProvider)
    p._client = FakeClient()
    p._slots = None
    got = p.list_settings("staging")
    assert got == [AppSetting("K", "v", slot_setting=True)]
    assert p.list_slots() == ["production", "staging"]
