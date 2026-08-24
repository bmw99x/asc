"""Tests for pure logic extracted from screen modals."""

from asc.models import Action, ActionKind
from asc.screens.add import parse_kv_input
from asc.screens.save_confirm import diff_line


def test_parse_kv_input():
    assert parse_kv_input("kv-prod/Db") == ("kv-prod", "Db")
    assert parse_kv_input("nope") is None


def test_diff_line_toggle_sticky():
    line = diff_line(Action(ActionKind.TOGGLE_STICKY, "K", "v", slot_setting=True))
    assert "slot setting" in line.plain and "on" in line.plain


def test_diff_line_set_shows_slot_suffix():
    line = diff_line(Action(ActionKind.SET, "K", "v", slot_setting=True))
    assert "[slot]" in line.plain
