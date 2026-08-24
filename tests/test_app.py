"""Headless TUI tests covering the critical asc user journeys."""

from typing import cast

from textual.widgets import Input, OptionList

from asc.app import AscApp, main
from asc.azure.client import AzureClientError
from asc.config import Config, ConfigError
from asc.constants import DEFAULT_APP, DEFAULT_GROUP, MOCK_DATA, PRODUCTION
from asc.models import ActionKind, AppSetting, KeyVaultRef, compose_kv_ref
from asc.providers import MockProvider
from asc.screens.confirm import ConfirmScreen
from asc.screens.context_picker import ContextPickerScreen
from asc.screens.save_confirm import SaveConfirmScreen, diff_line
from asc.widgets.settings_table import SettingsTable

PROD_ROWS = len(MOCK_DATA[DEFAULT_GROUP][DEFAULT_APP][PRODUCTION])
STAGING_ROWS = len(MOCK_DATA[DEFAULT_GROUP][DEFAULT_APP]["staging"])


async def wait_loaded(pilot) -> None:
    """Wait for all background workers (load / navigate / save) to finish."""
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def table_of(pilot) -> SettingsTable:
    """Return the settings table widget."""
    return pilot.app.query_one("#env-table", SettingsTable)


def badge_of(pilot, key: str) -> str:
    """Return the plain badge text for the row with the given key."""
    table = table_of(pilot)
    for row in range(table.row_count):
        coord = table.cursor_coordinate._replace(row=row, column=0)
        if str(table.get_cell_at(coord)) == key:
            badge = table.get_cell_at(coord._replace(column=2))
            return getattr(badge, "plain", str(badge))
    raise AssertionError(f"row {key} not in table")


def keys_in_order(pilot) -> list[str]:
    """Return the table's row keys in the order they appear on screen."""
    return [str(row.key.value) for row in table_of(pilot).ordered_rows]


async def move_to(pilot, key: str) -> None:
    """Move the table cursor onto the row with the given key."""
    table = table_of(pilot)
    for _ in range(table.row_count):
        if table.selected_key == key:
            return
        await pilot.press("j")
    assert table.selected_key == key


async def pick_context(pilot, group: str, app: str) -> None:
    """Drive the already-open context picker to select *group*/*app*."""
    screen = pilot.app.screen
    assert isinstance(screen, ContextPickerScreen)
    option_list = screen.query_one("#picker-list", OptionList)
    option_list.highlighted = option_list.get_option_index(f"{group}/{app}")
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def messages(pilot) -> list[str]:
    """Return the messages of all notifications raised so far."""
    return [n.message for n in pilot.app._notifications]  # noqa: SLF001


class _FailingProvider:
    """Provider that raises AzureClientError on apply (and optionally on list)."""

    def __init__(
        self,
        fail_on_list: bool = False,
        fail_on_slots: bool = False,
        fail_on_resolve: bool = False,
    ) -> None:
        self._inner = MockProvider(DEFAULT_GROUP, DEFAULT_APP)
        self._fail_on_list = fail_on_list
        self._fail_on_slots = fail_on_slots
        self._fail_on_resolve = fail_on_resolve

    def list_slots(self) -> list[str]:
        if self._fail_on_slots:
            raise AzureClientError("simulated slot discovery failure")
        return self._inner.list_slots()

    def list_settings(self, slot: str) -> list[AppSetting]:
        if self._fail_on_list:
            raise AzureClientError("simulated list failure")
        return self._inner.list_settings(slot)

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        raise AzureClientError("simulated write failure")

    def resolve_kv(self, ref: KeyVaultRef) -> str:
        if self._fail_on_resolve:
            raise AzureClientError("simulated resolve failure")
        return self._inner.resolve_kv(ref)


class _PostApplyProvider(MockProvider):
    """Provider that, once written to, returns sorted settings plus a new one.

    Stands in for Azure normalizing the order and another actor (the platform,
    a colleague) adding a setting behind the app's back.
    """

    def __init__(self) -> None:
        super().__init__()
        self._applied = False

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        super().apply(slot, upserts, deletes)
        self._applied = True

    def list_settings(self, slot: str) -> list[AppSetting]:
        settings = super().list_settings(slot)
        if self._applied:
            settings.append(AppSetting(key="WEBSITE_PLATFORM", value="managed"))
        return sorted(settings, key=lambda s: s.key, reverse=True)


class _RefreshFailProvider(MockProvider):
    """Provider whose apply succeeds but whose next list_settings fails."""

    def __init__(self) -> None:
        super().__init__()
        self._applied = False

    def apply(self, slot: str, upserts: list[AppSetting], deletes: list[str]) -> None:
        super().apply(slot, upserts, deletes)
        self._applied = True

    def list_settings(self, slot: str) -> list[AppSetting]:
        if self._applied:
            raise AzureClientError("simulated list failure")
        return super().list_settings(slot)


class _UnsortedProvider(MockProvider):
    """Provider that returns settings in a deliberately unsorted order."""

    def list_settings(self, slot: str) -> list[AppSetting]:
        return list(reversed(super().list_settings(slot)))


class TestMount:
    async def test_table_populated_on_mount(self):
        """
        GIVEN the app is launched with the default MockProvider
        WHEN the UI mounts
        THEN the table holds one row per production setting
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            assert table_of(pilot).row_count == PROD_ROWS

    async def test_badges_rendered_for_kv_and_sticky_rows(self):
        """
        GIVEN the production mock data has one Key Vault, slot-sticky setting
        WHEN the UI mounts
        THEN that row shows both badges and a plain row shows none
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            assert "[KV]" in badge_of(pilot, "DATABASE_URL")
            assert "[slot]" in badge_of(pilot, "DATABASE_URL")
            assert badge_of(pilot, "APP_ENV") == ""

    async def test_search_hidden_and_table_focused_on_mount(self):
        """
        GIVEN the app is launched
        WHEN the initial load completes
        THEN the search bar is hidden, loading is off, and the table has focus
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            assert pilot.app.query_one("#search", Input).display is False
            assert app.loading is False
            assert isinstance(pilot.app.focused, SettingsTable)

    async def test_slots_discovered_from_provider(self):
        """
        GIVEN a provider exposing production and staging slots
        WHEN the app mounts
        THEN the discovered slot list drives the tab bar and production is active
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            assert app._slots == [PRODUCTION, "staging"]  # noqa: SLF001
            assert app.current_slot == PRODUCTION

    async def test_slot_discovery_failure_falls_back_to_production(self):
        """
        GIVEN a provider whose list_slots raises AzureClientError
        WHEN the app mounts
        THEN a warning is notified and the slot list falls back to production only
        """
        provider = _FailingProvider(fail_on_slots=True)
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            assert app._slots == [PRODUCTION]  # noqa: SLF001
            assert any("slot" in m.lower() for m in messages(pilot))

    async def test_load_failure_leaves_table_empty(self):
        """
        GIVEN a provider whose list_settings raises AzureClientError
        WHEN the app mounts
        THEN the app survives and the table is empty
        """
        provider = _FailingProvider(fail_on_list=True)
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            assert table_of(pilot).row_count == 0


class TestStickyToggle:
    async def test_t_stages_sticky_toggle(self):
        """
        GIVEN the cursor is on a non-sticky setting
        WHEN the user presses t
        THEN dirty is set, one action is staged, and the [slot] badge appears
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "APP_ENV")

            await pilot.press("t")
            await pilot.pause()

            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001
            assert "[slot]" in badge_of(pilot, "APP_ENV")

    async def test_u_reverts_sticky_toggle(self):
        """
        GIVEN a staged sticky toggle
        WHEN the user presses u
        THEN the flag flips back, the badge clears, and dirty is False
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "APP_ENV")
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("u")
            await pilot.pause()

            assert app.dirty is False
            assert app._undo_stack == []  # noqa: SLF001
            assert badge_of(pilot, "APP_ENV") == ""

    async def test_t_untoggles_an_already_sticky_setting(self):
        """
        GIVEN the cursor is on a slot-sticky setting
        WHEN the user presses t
        THEN the [slot] badge is removed
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("t")
            await pilot.pause()

            assert "[slot]" not in badge_of(pilot, "DATABASE_URL")


class TestCursorStability:
    async def test_staging_a_toggle_keeps_the_cursor_on_the_same_row(self):
        """
        GIVEN the cursor is on a row other than the first
        WHEN a sticky toggle is staged (which repopulates the table)
        THEN the cursor is still on that same setting
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "LOG_LEVEL")

            await pilot.press("t")
            await pilot.pause()

            assert table_of(pilot).selected_key == "LOG_LEVEL"

    async def test_dd_on_a_middle_row_leaves_cursor_on_the_next_row(self):
        """
        GIVEN the cursor is on a middle row
        WHEN the user deletes it with dd
        THEN the cursor lands on the row that took its place (vim dd behaviour)
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            assert table_of(pilot).selected_key == "LOG_LEVEL"

    async def test_dd_twice_deletes_two_adjacent_settings(self):
        """
        GIVEN the cursor is on a middle row
        WHEN the user presses dd twice
        THEN the two adjacent settings are staged for deletion, not the first row
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            keys = {s.key for s in app._all_settings}  # noqa: SLF001
            assert keys == {"APP_ENV", "NEW_TO_DELETE"}

    async def test_dd_on_the_last_row_clamps_to_the_new_last_row(self):
        """
        GIVEN the cursor is on the final row
        WHEN it is deleted
        THEN the cursor clamps back onto the new final row
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "NEW_TO_DELETE")

            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            assert table_of(pilot).selected_key == "LOG_LEVEL"


class TestPrefixKeys:
    async def test_d_then_movement_then_d_stages_nothing(self):
        """
        GIVEN a half-typed dd
        WHEN the user moves the cursor before completing it
        THEN the pending d is forgotten and nothing is deleted
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("d")
            await pilot.press("j")
            await pilot.press("d")
            await pilot.pause()

            assert app._undo_stack == []  # noqa: SLF001
            assert table_of(pilot).row_count == PROD_ROWS

    async def test_d_then_another_action_then_d_stages_nothing(self):
        """
        GIVEN a half-typed dd
        WHEN an unrelated action (copy) runs before the second d
        THEN the pending d is forgotten
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("d")
            await pilot.press("y")
            await pilot.press("d")
            await pilot.pause()

            assert app._undo_stack == []  # noqa: SLF001

    async def test_g_then_movement_then_g_does_not_jump_to_top(self):
        """
        GIVEN a half-typed gg
        WHEN the user moves the cursor before completing it
        THEN the second g starts a fresh gg instead of jumping
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "LOG_LEVEL")

            await pilot.press("g")
            await pilot.press("j")
            await pilot.press("g")
            await pilot.pause()

            assert table_of(pilot).selected_key == "NEW_TO_DELETE"

    async def test_dd_still_works_when_pressed_consecutively(self):
        """
        GIVEN no intervening keys
        WHEN dd is pressed
        THEN the selected setting is still staged for deletion
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            assert len(app._undo_stack) == 1  # noqa: SLF001


class TestSaveFlow:
    async def test_add_edit_delete_then_save_writes_net_changes(self):
        """
        GIVEN staged add, edit and delete changes
        WHEN the user presses s and confirms with y
        THEN one batched apply lands all three changes in the provider
        """
        provider = MockProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            # Add
            await pilot.press("o")
            for ch in "NEW_KEY":
                await pilot.press(ch)
            await pilot.press("enter")
            for ch in "hello":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()

            # Edit LOG_LEVEL
            await move_to(pilot, "LOG_LEVEL")
            await pilot.press("i")
            await pilot.pause()
            pilot.app.screen.query_one("#edit-value", Input).value = "warn"
            await pilot.press("enter")
            await pilot.pause()

            # Delete NEW_TO_DELETE
            await move_to(pilot, "NEW_TO_DELETE")
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            assert len(app._undo_stack) == 3  # noqa: SLF001

            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SaveConfirmScreen)
            await pilot.press("y")
            await wait_loaded(pilot)

            saved = {s.key: s for s in provider.list_settings(PRODUCTION)}
            assert saved["NEW_KEY"].value == "hello"
            assert saved["LOG_LEVEL"].value == "warn"
            assert "NEW_TO_DELETE" not in saved
            assert app.dirty is False
            assert app._undo_stack == []  # noqa: SLF001

    async def test_save_rebaselines_so_second_save_is_a_noop(self):
        """
        GIVEN a completed save
        WHEN the user presses s again without further edits
        THEN no SaveConfirmScreen is pushed
        """
        provider = MockProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "APP_ENV")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            await pilot.press("s")
            await pilot.pause()

            assert not isinstance(pilot.app.screen, SaveConfirmScreen)
            assert provider.list_settings(PRODUCTION)[0].slot_setting is True

    async def test_sticky_toggle_is_persisted_on_save(self):
        """
        GIVEN a staged sticky toggle
        WHEN the user saves
        THEN the provider records the new slotSetting flag
        """
        provider = MockProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "LOG_LEVEL")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            saved = {s.key: s for s in provider.list_settings(PRODUCTION)}
            assert saved["LOG_LEVEL"].slot_setting is True

    async def test_rename_then_save_deletes_old_and_writes_new(self):
        """
        GIVEN a staged rename
        WHEN the user saves
        THEN the old key is gone and the new key carries the old value
        """
        provider = MockProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "LOG_LEVEL")

            await pilot.press("r")
            await pilot.pause()
            pilot.app.screen.query_one("#rename-key", Input).value = "LOGLEVEL"
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            saved = {s.key: s for s in provider.list_settings(PRODUCTION)}
            assert "LOG_LEVEL" not in saved
            assert saved["LOGLEVEL"].value == "info"

    async def test_save_failure_keeps_stage_and_dirty(self):
        """
        GIVEN a provider whose apply raises AzureClientError
        WHEN the user confirms a save
        THEN the error is notified and the stage and dirty flag survive
        """
        provider = _FailingProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SaveConfirmScreen)
            await pilot.press("y")
            await wait_loaded(pilot)

            app = cast(AscApp, pilot.app)
            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001
            assert any("simulated write failure" in m for m in messages(pilot))

    async def test_declining_the_save_confirm_writes_nothing(self):
        """
        GIVEN staged changes and the SaveConfirmScreen open
        WHEN the user declines with n
        THEN the provider is untouched and the stage and dirty flag survive
        """
        provider = MockProvider()
        before = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "NEW_TO_DELETE")
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SaveConfirmScreen)
            await pilot.press("n")
            await wait_loaded(pilot)

            after = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
            assert after == before
            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001

    async def test_escaping_the_save_confirm_writes_nothing(self):
        """
        GIVEN staged changes and the SaveConfirmScreen open
        WHEN the user presses Escape
        THEN the provider is untouched and the stage survives
        """
        provider = MockProvider()
        before = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()
            assert isinstance(pilot.app.screen, SaveConfirmScreen)
            await pilot.press("escape")
            await wait_loaded(pilot)

            after = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
            assert after == before
            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001

    async def test_staging_alone_writes_nothing_to_the_provider(self):
        """
        GIVEN a loaded app
        WHEN the user stages a sticky toggle, an add and a delete without saving
        THEN the provider still holds exactly the original settings
        """
        provider = MockProvider()
        before = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await move_to(pilot, "APP_ENV")
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("o")
            for ch in "STAGED_ONLY":
                await pilot.press(ch)
            await pilot.press("enter")
            for ch in "nope":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()

            await move_to(pilot, "NEW_TO_DELETE")
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            assert len(app._undo_stack) == 3  # noqa: SLF001
            after = {s.key: (s.value, s.slot_setting) for s in provider.list_settings(PRODUCTION)}
            assert after == before
            assert "STAGED_ONLY" not in after

    async def test_save_with_no_changes_notifies(self):
        """
        GIVEN a clean app
        WHEN the user presses s
        THEN no modal opens and the user is told there is nothing to save
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("s")
            await pilot.pause()

            assert not isinstance(pilot.app.screen, SaveConfirmScreen)
            assert any("no unsaved changes" in m.lower() for m in messages(pilot))


class TestSaveRefresh:
    async def test_save_reloads_settings_from_the_provider(self):
        """
        GIVEN a provider that reorders settings and adds one of its own on apply
        WHEN the user confirms a save
        THEN the table and the baseline hold the freshly fetched settings
        """
        provider = _PostApplyProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "LOG_LEVEL")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            fetched = provider.list_settings(PRODUCTION)
            assert keys_in_order(pilot) == [s.key for s in fetched]
            assert "WEBSITE_PLATFORM" in keys_in_order(pilot)
            assert app._all_settings == fetched  # noqa: SLF001
            assert app._baseline == fetched  # noqa: SLF001

    async def test_save_refresh_keeps_cursor_on_the_selected_key(self):
        """
        GIVEN the cursor is on LOG_LEVEL when a save is confirmed
        WHEN the post-save refetch reorders the rows
        THEN the cursor is still on LOG_LEVEL
        """
        async with AscApp(provider=_PostApplyProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "LOG_LEVEL")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            assert table_of(pilot).selected_key == "LOG_LEVEL"

    async def test_refresh_failure_after_save_keeps_working_copy_and_warns(self):
        """
        GIVEN a provider whose apply succeeds but whose next list raises
        WHEN the user confirms a save
        THEN the post-save working copy survives and a warning is raised
        """
        provider = _RefreshFailProvider()
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "LOG_LEVEL")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await wait_loaded(pilot)

            assert app.dirty is False
            assert app._undo_stack == []  # noqa: SLF001
            assert table_of(pilot).row_count == PROD_ROWS
            local = {s.key: s.slot_setting for s in app._all_settings}  # noqa: SLF001
            assert local["LOG_LEVEL"] is True
            assert app._baseline == app._all_settings  # noqa: SLF001
            assert any("refresh failed" in m.lower() for m in messages(pilot))


class TestQuit:
    async def test_q_with_no_changes_quits_immediately(self):
        """
        GIVEN a clean stage
        WHEN the user presses q
        THEN the app exits without a prompt
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("q")
            await pilot.pause()

            assert not isinstance(pilot.app.screen, ConfirmScreen)
            assert pilot.app.is_running is False

    async def test_q_when_dirty_prompts_and_declining_keeps_the_stage(self):
        """
        GIVEN staged changes
        WHEN the user presses q and declines the prompt
        THEN the app is still running with the stage intact
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("q")
            await pilot.pause()
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("n")
            await pilot.pause()

            assert app.is_running is True
            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001

    async def test_q_when_dirty_quits_on_confirm(self):
        """
        GIVEN staged changes
        WHEN the user presses q and confirms
        THEN the app exits, discarding the stage
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("q")
            await pilot.pause()
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("y")
            await pilot.pause()

            assert pilot.app.is_running is False


class TestNetChanges:
    """Unit tests for the baseline → net diff collapse (no Pilot)."""

    def _app(self, baseline: list[AppSetting], current: list[AppSetting]) -> AscApp:
        app = AscApp(provider=MockProvider())
        app._baseline = baseline  # noqa: SLF001
        app._all_settings = current  # noqa: SLF001
        return app

    def test_no_changes_yields_empty_diff(self):
        """
        GIVEN the working copy equals the baseline
        WHEN net changes are computed
        THEN both upserts and deletes are empty
        """
        baseline = [AppSetting("A", "1"), AppSetting("B", "2", slot_setting=True)]
        app = self._app(baseline, [AppSetting("A", "1"), AppSetting("B", "2", slot_setting=True)])
        assert app._net_changes() == ([], [])  # noqa: SLF001

    def test_edit_then_undo_yields_empty_diff(self):
        """
        GIVEN a value was edited and then restored (undo)
        WHEN net changes are computed
        THEN nothing is written
        """
        app = self._app([AppSetting("A", "1")], [AppSetting("A", "2")])
        app._all_settings[0].value = "1"  # noqa: SLF001
        assert app._net_changes() == ([], [])  # noqa: SLF001

    def test_new_key_yields_upsert(self):
        """
        GIVEN a key absent from the baseline
        WHEN net changes are computed
        THEN it is upserted and nothing is deleted
        """
        app = self._app([], [AppSetting("A", "1")])
        upserts, deletes = app._net_changes()  # noqa: SLF001
        assert [s.key for s in upserts] == ["A"]
        assert deletes == []

    def test_rename_yields_delete_and_upsert(self):
        """
        GIVEN a key was renamed
        WHEN net changes are computed
        THEN the old key is deleted and the new key upserted
        """
        app = self._app([AppSetting("OLD", "v")], [AppSetting("NEW", "v")])
        upserts, deletes = app._net_changes()  # noqa: SLF001
        assert [s.key for s in upserts] == ["NEW"]
        assert deletes == ["OLD"]

    def test_sticky_only_change_yields_upsert_with_flag(self):
        """
        GIVEN only the slot_setting flag changed
        WHEN net changes are computed
        THEN the setting is upserted carrying the new flag
        """
        app = self._app([AppSetting("A", "1")], [AppSetting("A", "1", slot_setting=True)])
        upserts, deletes = app._net_changes()  # noqa: SLF001
        assert deletes == []
        assert upserts[0].slot_setting is True

    def test_delete_yields_delete_only(self):
        """
        GIVEN a baseline key removed from the working copy
        WHEN net changes are computed
        THEN it appears only in deletes
        """
        app = self._app([AppSetting("A", "1"), AppSetting("B", "2")], [AppSetting("A", "1")])
        upserts, deletes = app._net_changes()  # noqa: SLF001
        assert upserts == []
        assert deletes == ["B"]


class TestSaveConfirmDiff:
    """The confirm diff must describe the net write, not the undo history."""

    def _app(self, baseline: list[AppSetting], current: list[AppSetting]) -> AscApp:
        app = AscApp(provider=MockProvider())
        app._baseline = baseline  # noqa: SLF001
        app._all_settings = current  # noqa: SLF001
        return app

    def test_new_key_shows_an_add_line(self):
        """
        GIVEN a key absent from the baseline
        WHEN the diff is built
        THEN one add (SET with no previous value) line is shown
        """
        app = self._app([], [AppSetting("A", "1")])
        actions = app._diff_actions()  # noqa: SLF001
        assert [(a.kind, a.key, a.previous_value) for a in actions] == [
            (ActionKind.SET, "A", None)
        ]

    def test_edited_key_shows_an_edit_line_with_both_values(self):
        """
        GIVEN a baseline key whose value changed
        WHEN the diff is built
        THEN the line carries the old and new values
        """
        app = self._app([AppSetting("A", "1")], [AppSetting("A", "2")])
        (action,) = app._diff_actions()  # noqa: SLF001
        assert (action.kind, action.previous_value, action.value) == (ActionKind.SET, "1", "2")
        assert "1 → 2" in diff_line(action).plain

    def test_sticky_only_change_shows_one_toggle_line(self):
        """
        GIVEN only the slot_setting flag changed
        WHEN the diff is built
        THEN a single sticky-toggle line is shown, not a value edit
        """
        app = self._app([AppSetting("A", "1")], [AppSetting("A", "1", slot_setting=True)])
        (action,) = app._diff_actions()  # noqa: SLF001
        assert action.kind == ActionKind.TOGGLE_STICKY
        assert "slot setting → on" in diff_line(action).plain

    def test_delete_shows_the_value_being_lost(self):
        """
        GIVEN a baseline key removed from the working copy
        WHEN the diff is built
        THEN a delete line naming its value is shown
        """
        app = self._app([AppSetting("A", "secret-ish")], [])
        (action,) = app._diff_actions()  # noqa: SLF001
        assert action.kind == ActionKind.DELETE
        assert "secret-ish" in diff_line(action).plain

    def test_edit_then_undo_shows_no_lines(self):
        """
        GIVEN a value that was edited and restored
        WHEN the diff is built
        THEN nothing is listed
        """
        app = self._app([AppSetting("A", "1")], [AppSetting("A", "1")])
        assert app._diff_actions() == []  # noqa: SLF001

    async def test_add_toggle_then_delete_the_same_key_saves_nothing(self):
        """
        GIVEN a key that was added, toggled sticky, and then deleted again
        WHEN the user presses s
        THEN no confirm modal opens and the stage is reported as empty
        """
        provider = MockProvider()
        before = {s.key for s in provider.list_settings(PRODUCTION)}
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("o")
            for ch in "TEMP":
                await pilot.press(ch)
            await pilot.press("enter")
            for ch in "x":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()
            await move_to(pilot, "TEMP")
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("d")
            await pilot.press("d")
            await pilot.pause()

            await pilot.press("s")
            await pilot.pause()

            assert not isinstance(pilot.app.screen, SaveConfirmScreen)
            assert any("nothing to save" in m.lower() for m in messages(pilot))
            assert app.dirty is False
            assert {s.key for s in provider.list_settings(PRODUCTION)} == before

    async def test_confirm_diff_lists_the_net_write_not_the_history(self):
        """
        GIVEN a value edited twice over
        WHEN the confirm screen opens
        THEN it lists one line for that key, not one per keystroke history entry
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "LOG_LEVEL")
            for value in ("warn", "error"):
                await pilot.press("i")
                await pilot.pause()
                pilot.app.screen.query_one("#edit-value", Input).value = value
                await pilot.press("enter")
                await pilot.pause()

            assert len(app._undo_stack) == 2  # noqa: SLF001

            await pilot.press("s")
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, SaveConfirmScreen)
            assert len(screen._actions) == 1  # noqa: SLF001
            assert "info → error" in diff_line(screen._actions[0]).plain  # noqa: SLF001


class TestSlotSwitching:
    async def test_e_cycles_to_next_slot_and_reloads(self):
        """
        GIVEN the app is on the production slot
        WHEN the user presses e
        THEN the staging slot loads and the table shows staging data
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            assert app.current_slot == PRODUCTION

            await pilot.press("e")
            await wait_loaded(pilot)

            assert app.current_slot == "staging"
            assert table_of(pilot).row_count == STAGING_ROWS
            values = {s.key: s.value for s in app._all_settings}  # noqa: SLF001
            assert values["APP_ENV"] == "staging"

    async def test_e_wraps_back_to_production(self):
        """
        GIVEN the app is on the last slot
        WHEN the user presses e
        THEN the slot wraps around to production
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("e")
            await wait_loaded(pilot)
            await pilot.press("e")
            await wait_loaded(pilot)
            assert app.current_slot == PRODUCTION

    async def test_slot_switch_clears_the_stage(self):
        """
        GIVEN unsaved changes and a confirmed slot switch
        WHEN the new slot loads
        THEN the stage is cleared and dirty is False
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()
            assert app.dirty is True

            await pilot.press("e")
            await pilot.pause()
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("y")
            await wait_loaded(pilot)

            assert app.current_slot == "staging"
            assert app.dirty is False
            assert app._undo_stack == []  # noqa: SLF001

    async def test_dirty_switch_can_be_cancelled(self):
        """
        GIVEN unsaved changes
        WHEN the user declines the switch confirmation
        THEN the slot and the stage are untouched
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("e")
            await pilot.pause()
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("n")
            await wait_loaded(pilot)

            assert app.current_slot == PRODUCTION
            assert app.dirty is True

    async def test_clicking_a_slot_tab_navigates(self):
        """
        GIVEN the app is on production
        WHEN the user clicks the staging tab
        THEN the staging slot loads
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.click("#tab-staging")
            await wait_loaded(pilot)

            assert app.current_slot == "staging"


class TestSaveNavigateRace:
    async def test_slot_switch_is_refused_while_a_save_is_running(self):
        """
        GIVEN a save that is still writing to the provider
        WHEN the user tries to switch slot
        THEN the switch is refused with a warning and the slot is unchanged
        """
        import time

        class _SlowApplyProvider(MockProvider):
            def apply(
                self, slot: str, upserts: list[AppSetting], deletes: list[str]
            ) -> None:
                time.sleep(0.3)
                super().apply(slot, upserts, deletes)

        async with AscApp(provider=_SlowApplyProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert app._save_in_progress() is True  # noqa: SLF001

            await pilot.press("e")
            await pilot.pause()

            assert app.current_slot == PRODUCTION
            assert any("save in progress" in m.lower() for m in messages(pilot))

            await wait_loaded(pilot)
            assert app.dirty is False


class TestContextSwitching:
    async def test_app_switch_resets_slot_to_production(self):
        """
        GIVEN the app is on the staging slot
        WHEN a different app in the same group is selected
        THEN the slot resets to production and that app's settings load
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("e")
            await wait_loaded(pilot)
            assert app.current_slot == "staging"

            app._navigate_to(DEFAULT_GROUP, "web", PRODUCTION)  # noqa: SLF001
            await wait_loaded(pilot)

            assert app.current_app == "web"
            assert app.current_slot == PRODUCTION
            expected = len(MOCK_DATA[DEFAULT_GROUP]["web"][PRODUCTION])
            assert table_of(pilot).row_count == expected

    async def test_group_switch_loads_other_group_settings(self):
        """
        GIVEN the default group is active
        WHEN a different group and app are selected
        THEN the table shows that group's app settings
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            app._navigate_to("Internal", "admin", PRODUCTION)  # noqa: SLF001
            await wait_loaded(pilot)

            assert app.current_group == "Internal"
            expected = len(MOCK_DATA["Internal"]["admin"][PRODUCTION])
            assert table_of(pilot).row_count == expected

    async def test_picker_app_switch_resets_slot_to_production(self):
        """
        GIVEN the app is on MyProduct/api's staging slot
        WHEN the user picks MyProduct/web with p
        THEN the slot resets to production and web's settings load
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("e")
            await wait_loaded(pilot)
            assert app.current_slot == "staging"

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, DEFAULT_GROUP, "web")
            await wait_loaded(pilot)

            assert app.current_app == "web"
            assert app.current_slot == PRODUCTION
            expected = len(MOCK_DATA[DEFAULT_GROUP]["web"][PRODUCTION])
            assert table_of(pilot).row_count == expected

    async def test_picker_group_switch_loads_other_group(self):
        """
        GIVEN the default group is active
        WHEN the user picks Internal/admin with p
        THEN that group's app settings load on the production slot
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, "Internal", "admin")
            await wait_loaded(pilot)

            assert (app.current_group, app.current_app) == ("Internal", "admin")
            assert app.current_slot == PRODUCTION
            expected = len(MOCK_DATA["Internal"]["admin"][PRODUCTION])
            assert table_of(pilot).row_count == expected

    async def test_picker_switch_is_guarded_when_dirty(self):
        """
        GIVEN unsaved changes
        WHEN the user picks another app and declines the confirmation
        THEN the context and the stage are untouched
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, DEFAULT_GROUP, "web")
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("n")
            await wait_loaded(pilot)

            assert app.current_app == DEFAULT_APP
            assert app.dirty is True
            assert len(app._undo_stack) == 1  # noqa: SLF001

    async def test_picker_switch_confirmed_when_dirty_clears_stage(self):
        """
        GIVEN unsaved changes
        WHEN the user picks another app and confirms the switch
        THEN the new app loads with a clean stage
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("t")
            await pilot.pause()

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, DEFAULT_GROUP, "web")
            assert isinstance(pilot.app.screen, ConfirmScreen)
            await pilot.press("y")
            await wait_loaded(pilot)

            assert app.current_app == "web"
            assert app.dirty is False
            assert app._undo_stack == []  # noqa: SLF001

    async def test_picking_the_current_app_does_not_navigate(self):
        """
        GIVEN the app is on the staging slot
        WHEN the user picks the app that is already active
        THEN nothing reloads and the slot is left alone
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await pilot.press("e")
            await wait_loaded(pilot)

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, DEFAULT_GROUP, DEFAULT_APP)
            await wait_loaded(pilot)

            assert app.current_slot == "staging"
            assert isinstance(pilot.app.focused, SettingsTable)

    async def test_clicking_the_app_label_opens_the_picker(self):
        """
        GIVEN the app has loaded
        WHEN the user clicks the app label in the slot bar
        THEN the context picker opens
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)

            await pilot.click("#slot-tabs-app")
            await pilot.pause()

            assert isinstance(pilot.app.screen, ContextPickerScreen)

    async def test_group_app_memory_restores_last_app_on_return(self):
        """
        GIVEN MyProduct/web was the last app used in that group
        WHEN navigation returns to MyProduct without a valid app for it
        THEN the remembered app (web) is restored, not the group's first app
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, DEFAULT_GROUP, "web")
            await wait_loaded(pilot)
            await pilot.press("p")
            await pilot.pause()
            await pick_context(pilot, "Internal", "admin")
            await wait_loaded(pilot)

            # "admin" does not exist under MyProduct, so _resolve_app falls back
            # to the app last used in that group rather than to apps[0] ("api").
            app._navigate_to(DEFAULT_GROUP, "admin", PRODUCTION)  # noqa: SLF001
            await wait_loaded(pilot)

            assert app.current_app == "web"
            expected = len(MOCK_DATA[DEFAULT_GROUP]["web"][PRODUCTION])
            assert table_of(pilot).row_count == expected

    async def test_subtitle_reflects_group_app_and_slot(self):
        """
        GIVEN the app has loaded
        WHEN the subtitle is inspected
        THEN it names the group, app and slot
        """
        async with AscApp().run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            assert DEFAULT_GROUP in app.sub_title
            assert DEFAULT_APP in app.sub_title
            assert PRODUCTION in app.sub_title


class TestCopy:
    async def test_y_copies_raw_reference_for_kv_row(self):
        """
        GIVEN the cursor is on a Key Vault reference row
        WHEN the user presses y
        THEN the raw @Microsoft.KeyVault(...) reference is copied
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("y")
            await pilot.pause()

            assert pilot.app.clipboard == compose_kv_ref("kv-myproduct-prod", "database-url")

    async def test_y_copies_plain_value(self):
        """
        GIVEN the cursor is on a plain row
        WHEN the user presses y
        THEN the value is copied verbatim
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "APP_ENV")

            await pilot.press("y")
            await pilot.pause()

            assert pilot.app.clipboard == "production"

    async def test_shift_y_on_plain_row_warns(self):
        """
        GIVEN the cursor is on a plain row
        WHEN the user presses Y
        THEN a warning is shown and nothing is copied
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "APP_ENV")

            await pilot.press("Y")
            await wait_loaded(pilot)

            assert pilot.app.clipboard == ""
            assert any("not a key vault reference" in m.lower() for m in messages(pilot))

    async def test_shift_y_on_kv_row_copies_resolved_value(self):
        """
        GIVEN the cursor is on a Key Vault reference row
        WHEN the user presses Y
        THEN the resolved secret value is copied
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("Y")
            await wait_loaded(pilot)

            assert pilot.app.clipboard == "mock-secret-value::kv-myproduct-prod/database-url"

    async def test_shift_y_reports_resolve_failure(self):
        """
        GIVEN a provider whose resolve_kv raises AzureClientError
        WHEN the user presses Y on a Key Vault row
        THEN the failure is notified and nothing is copied
        """
        provider = _FailingProvider(fail_on_resolve=True)
        async with AscApp(provider=provider).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("Y")
            await wait_loaded(pilot)

            assert pilot.app.clipboard == ""
            assert any("simulated resolve failure" in m for m in messages(pilot))


class TestSearch:
    async def test_slash_opens_search_and_filters(self):
        """
        GIVEN the app has loaded
        WHEN the user opens search and types a query
        THEN only matching rows remain
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("/")
            assert pilot.app.query_one("#search", Input).display is True
            for ch in "LOG":
                await pilot.press(ch)
            await pilot.pause()
            assert table_of(pilot).row_count == 1

    async def test_escape_clears_filter(self):
        """
        GIVEN an active filter
        WHEN the user presses Escape
        THEN all rows return and the search bar hides
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("/")
            for ch in "LOG":
                await pilot.press(ch)
            await pilot.press("escape")
            await pilot.pause()
            assert table_of(pilot).row_count == PROD_ROWS
            assert pilot.app.query_one("#search", Input).display is False


class TestEditSticky:
    async def test_edit_changing_only_sticky_stages_toggle(self):
        """
        GIVEN the edit modal is open on a non-sticky setting
        WHEN only the sticky checkbox is ticked and saved
        THEN a TOGGLE_STICKY action is staged
        """
        from textual.widgets import Checkbox

        from asc.models import ActionKind
        from asc.screens.edit import EditScreen

        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "APP_ENV")

            await pilot.press("i")
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, EditScreen)
            screen.query_one("#sticky", Checkbox).toggle()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert len(app._undo_stack) == 1  # noqa: SLF001
            assert app._undo_stack[0].kind == ActionKind.TOGGLE_STICKY  # noqa: SLF001
            assert "[slot]" in badge_of(pilot, "APP_ENV")

    async def test_edit_changing_value_and_sticky_stages_set(self):
        """
        GIVEN the edit modal is open on a non-sticky setting
        WHEN both the value and the sticky checkbox change
        THEN a single SET action records both previous states
        """
        from textual.widgets import Checkbox

        from asc.models import ActionKind
        from asc.screens.edit import EditScreen

        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "APP_ENV")

            await pilot.press("i")
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, EditScreen)
            screen.query_one("#sticky", Checkbox).toggle()
            await pilot.pause()
            screen.query_one("#edit-value", Input).value = "prod2"
            await pilot.press("enter")
            await pilot.pause()

            stack = app._undo_stack  # noqa: SLF001
            assert len(stack) == 1
            assert stack[0].kind == ActionKind.SET
            assert stack[0].previous_value == "production"
            assert stack[0].previous_slot_setting is False
            assert stack[0].slot_setting is True

    async def test_undo_of_value_and_sticky_edit_restores_both(self):
        """
        GIVEN a staged SET that changed value and sticky flag
        WHEN the user presses u
        THEN both the value and the flag are restored
        """
        from textual.widgets import Checkbox

        from asc.screens.edit import EditScreen

        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            await move_to(pilot, "APP_ENV")

            await pilot.press("i")
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, EditScreen)
            screen.query_one("#sticky", Checkbox).toggle()
            await pilot.pause()
            screen.query_one("#edit-value", Input).value = "prod2"
            await pilot.press("enter")
            await pilot.pause()

            await pilot.press("u")
            await pilot.pause()

            setting = next(s for s in app._all_settings if s.key == "APP_ENV")  # noqa: SLF001
            assert setting.value == "production"
            assert setting.slot_setting is False
            assert app.dirty is False


class TestConfigStartup:
    async def test_missing_config_notifies_mock_fallback(self, monkeypatch):
        """
        GIVEN no usable config on disk
        WHEN the app is launched in config mode
        THEN mock data is shown and a one-off notification explains why
        """
        monkeypatch.setattr("asc.app.load_config", lambda: {})

        app = AscApp(_use_config=True)
        async with app.run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            assert app._using_mock is True  # noqa: SLF001
            assert table_of(pilot).row_count == PROD_ROWS
            assert any("No config found" in m for m in messages(pilot))

    async def test_invalid_config_is_not_swallowed(self, monkeypatch):
        """
        GIVEN a config file that exists but fails validation
        WHEN the app is constructed
        THEN the ConfigError surfaces instead of silently falling back to mocks
        """
        import pytest

        def boom() -> Config:
            raise ConfigError("config.json is not valid JSON: line 1")

        monkeypatch.setattr("asc.app.load_config", boom)

        with pytest.raises(ConfigError, match="not valid JSON"):
            AscApp(_use_config=True)

    def test_main_exits_nonzero_on_invalid_config(self, monkeypatch, capsys):
        """
        GIVEN a malformed config file
        WHEN the CLI entry point runs
        THEN it prints the error and exits non-zero without starting the TUI
        """
        import pytest

        def boom() -> Config:
            raise ConfigError("Invalid config for MyProduct/api: missing app_name")

        monkeypatch.setattr("asc.app.load_config", boom)
        monkeypatch.setattr(
            AscApp, "run", lambda self: pytest.fail("TUI must not start on invalid config")
        )

        with pytest.raises(SystemExit) as excinfo:
            main()

        assert excinfo.value.code == 1
        assert "missing app_name" in capsys.readouterr().err


class TestNavigationWorkers:
    async def test_slot_switch_has_no_artificial_delay(self):
        """
        GIVEN the app loaded against the in-memory mock provider
        WHEN a slot switch is performed
        THEN it completes promptly (no ported sleep on the navigation path)
        """
        import time

        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            started = time.perf_counter()
            app._navigate_to(DEFAULT_GROUP, DEFAULT_APP, "staging")  # noqa: SLF001
            await wait_loaded(pilot)
            elapsed = time.perf_counter() - started

            assert app.current_slot == "staging"
            assert elapsed < 0.3, f"navigation took {elapsed:.3f}s"

    async def test_rapid_slot_switches_do_not_interleave(self):
        """
        GIVEN two navigations kicked off back to back
        WHEN both workers have settled
        THEN the last one wins and the table matches that slot exactly
        """
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)

            app._navigate_to(DEFAULT_GROUP, DEFAULT_APP, "staging")  # noqa: SLF001
            app._navigate_to(DEFAULT_GROUP, DEFAULT_APP, PRODUCTION)  # noqa: SLF001
            await wait_loaded(pilot)

            assert app.current_slot == PRODUCTION
            assert table_of(pilot).row_count == PROD_ROWS
            assert app.loading is False


class TestEditKeyVaultQuirks:
    async def test_saving_an_unchanged_vaultname_ref_stages_nothing(self):
        """
        GIVEN a setting stored in VaultName/SecretName reference form
        WHEN the edit modal is opened and saved without changes
        THEN nothing is staged (no silent rewrite to SecretUri form)
        """
        from asc.screens.edit import EditScreen

        named_ref = "@Microsoft.KeyVault(VaultName=kv-myproduct-prod;SecretName=database-url)"
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            setting = next(s for s in app._all_settings if s.key == "DATABASE_URL")  # noqa: SLF001
            setting.value = named_ref
            app._refresh_table()  # noqa: SLF001
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("i")
            await pilot.pause()
            assert isinstance(pilot.app.screen, EditScreen)
            await pilot.press("enter")
            await pilot.pause()

            assert app._undo_stack == []  # noqa: SLF001
            assert app.dirty is False
            assert setting.value == named_ref

    async def test_unchecking_kv_mode_restores_the_raw_value(self):
        """
        GIVEN the edit modal pre-filled with "vault/secret" for a KV reference
        WHEN the Key Vault reference checkbox is unticked
        THEN the input is restored to the original raw reference string
        """
        from textual.widgets import Checkbox

        from asc.screens.edit import EditScreen

        raw = compose_kv_ref("kv-myproduct-prod", "database-url")
        async with AscApp(provider=MockProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await move_to(pilot, "DATABASE_URL")

            await pilot.press("i")
            await pilot.pause()
            screen = pilot.app.screen
            assert isinstance(screen, EditScreen)
            assert screen.query_one("#edit-value", Input).value == (
                "kv-myproduct-prod/database-url"
            )

            screen.query_one("#kv-mode", Checkbox).toggle()
            await pilot.pause()

            assert screen.query_one("#edit-value", Input).value == raw


class TestBlockingProviderIo:
    async def test_event_loop_keeps_running_during_a_slow_provider_call(self):
        """
        GIVEN a provider whose list_settings blocks for 300ms
        WHEN a navigation runs
        THEN app timers keep firing throughout, so the spinner stays animated
        """
        import time

        class _SlowProvider(MockProvider):
            def list_settings(self, slot: str) -> list[AppSetting]:
                time.sleep(0.3)
                return super().list_settings(slot)

        ticks: list[float] = []
        async with AscApp(provider=_SlowProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            app = cast(AscApp, pilot.app)
            app.set_interval(0.02, lambda: ticks.append(time.perf_counter()))
            await pilot.pause()

            app._navigate_to(DEFAULT_GROUP, DEFAULT_APP, "staging")  # noqa: SLF001
            await wait_loaded(pilot)

            assert app.current_slot == "staging"
            assert table_of(pilot).row_count == STAGING_ROWS
            gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
            assert gaps, "timer never fired"
            assert max(gaps) < 0.2, f"loop stalled for {max(gaps):.3f}s"


class TestSorting:
    AZURE_ORDER = ["NEW_TO_DELETE", "LOG_LEVEL", "DATABASE_URL", "APP_ENV"]
    ASC_ORDER = ["APP_ENV", "DATABASE_URL", "LOG_LEVEL", "NEW_TO_DELETE"]

    async def test_s_cycles_azure_then_ascending_then_descending(self):
        """
        GIVEN a provider whose settings arrive out of alphabetical order
        WHEN the user presses S three times
        THEN the rows go A-Z, then Z-A, then back to the provider's order
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            assert keys_in_order(pilot) == self.AZURE_ORDER

            await pilot.press("S")
            await pilot.pause()
            assert keys_in_order(pilot) == self.ASC_ORDER

            await pilot.press("S")
            await pilot.pause()
            assert keys_in_order(pilot) == list(reversed(self.ASC_ORDER))

            await pilot.press("S")
            await pilot.pause()
            assert keys_in_order(pilot) == self.AZURE_ORDER

    async def test_each_press_notifies_the_new_sort_mode(self):
        """
        GIVEN the table in the provider's order
        WHEN the user presses S
        THEN the new sort mode is notified
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()

            assert any("a-z" in m.lower() for m in messages(pilot))

    async def test_sort_does_not_reorder_the_working_copy(self):
        """
        GIVEN an active A-Z sort
        WHEN the working copy is inspected
        THEN it is still in the order the provider returned
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()
            app = cast(AscApp, pilot.app)

            assert [s.key for s in app._all_settings] == self.AZURE_ORDER  # noqa: SLF001

    async def test_sort_survives_a_staged_mutation(self):
        """
        GIVEN an active A-Z sort
        WHEN the user stages an add
        THEN the refreshed table is still sorted A-Z, new key included
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()

            await pilot.press("o")
            for ch in "AAA_NEW":
                await pilot.press(ch)
            await pilot.press("enter")
            for ch in "hello":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause()

            assert keys_in_order(pilot) == ["AAA_NEW", *self.ASC_ORDER]

    async def test_sort_applies_within_an_active_filter(self):
        """
        GIVEN an active A-Z sort
        WHEN a search filter is applied
        THEN the matching rows are still sorted A-Z
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()
            await pilot.press("/")
            await pilot.press("l")
            await pilot.pause()

            assert keys_in_order(pilot) == ["DATABASE_URL", "LOG_LEVEL", "NEW_TO_DELETE"]

    async def test_slot_switch_resets_to_provider_order(self):
        """
        GIVEN an active A-Z sort on production
        WHEN the user switches to the staging slot
        THEN staging is shown in the provider's order
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()

            await pilot.press("e")
            await wait_loaded(pilot)

            assert keys_in_order(pilot) == ["LOG_LEVEL", "DATABASE_URL", "APP_ENV"]

    async def test_app_switch_resets_to_provider_order(self):
        """
        GIVEN an active A-Z sort
        WHEN the user switches app
        THEN the reloaded table is in the provider's order again
        """
        async with AscApp(provider=_UnsortedProvider()).run_test(headless=True) as pilot:
            await wait_loaded(pilot)
            await pilot.press("S")
            await pilot.pause()
            app = cast(AscApp, pilot.app)

            app._navigate_to(DEFAULT_GROUP, "web", PRODUCTION)  # noqa: SLF001
            await wait_loaded(pilot)

            assert keys_in_order(pilot) == self.AZURE_ORDER
