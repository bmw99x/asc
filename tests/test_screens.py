"""Tests for pure logic extracted from screen modals."""

from asc.models import Action, ActionKind, compose_kv_ref, key_error
from asc.screens.add import parse_kv_input
from asc.screens.save_confirm import MAX_VALUE_WIDTH, diff_line, display_value


def test_parse_kv_input():
    assert parse_kv_input("kv-prod/Db") == ("kv-prod", "Db")
    assert parse_kv_input("nope") is None


def test_key_error_accepts_ordinary_keys():
    assert key_error("LOG_LEVEL") is None


def test_key_error_rejects_blank_key():
    assert key_error("") == "Key cannot be blank"


def test_key_error_rejects_leading_dash():
    """az would read a -prefixed name as a flag rather than a setting."""
    message = key_error("-LOG_LEVEL")
    assert message is not None and "-" in message


def test_display_value_renders_kv_as_vault_slash_secret():
    assert display_value(compose_kv_ref("kv-prod", "Db")) == "kv-prod/Db"


def test_display_value_elides_long_values():
    rendered = display_value("x" * 200)
    assert len(rendered) == MAX_VALUE_WIDTH and rendered.endswith("…")


def test_diff_line_edit_shows_old_and_new_values():
    line = diff_line(Action(ActionKind.SET, "K", "new", previous_value="old"))
    assert "old → new" in line.plain


def test_diff_line_delete_shows_the_value_being_lost():
    assert "gone" in diff_line(Action(ActionKind.DELETE, "K", "gone")).plain


def test_diff_line_toggle_sticky():
    line = diff_line(Action(ActionKind.TOGGLE_STICKY, "K", "v", slot_setting=True))
    assert "slot setting" in line.plain and "on" in line.plain


def test_diff_line_set_shows_slot_suffix():
    line = diff_line(Action(ActionKind.SET, "K", "v", slot_setting=True))
    assert "[slot]" in line.plain
