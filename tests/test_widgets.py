"""Tests for settings table badge/value rendering helpers."""

from asc.models import AppSetting, compose_kv_ref
from asc.widgets.settings_table import badge_text, value_text


def test_badges_kv_and_slot():
    s = AppSetting("K", compose_kv_ref("kv-prod", "Db"), slot_setting=True)
    assert "[KV]" in badge_text(s).plain and "[slot]" in badge_text(s).plain


def test_badges_plain():
    assert badge_text(AppSetting("K", "v")).plain == ""


def test_value_shows_vault_slash_secret_for_kv():
    s = AppSetting("K", compose_kv_ref("kv-prod", "Db"))
    assert value_text(s).plain == "kv-prod/Db"


def test_value_plain_passthrough():
    assert value_text(AppSetting("K", "v")).plain == "v"
