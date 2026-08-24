"""Headless TUI tests covering the critical asc user journeys."""

from typing import cast

from textual.widgets import Input, OptionList

from asc.app import AscApp
from asc.azure.client import AzureClientError
from asc.constants import DEFAULT_APP, DEFAULT_GROUP, MOCK_DATA, PRODUCTION
from asc.models import AppSetting, KeyVaultRef, compose_kv_ref
from asc.providers import MockProvider
from asc.screens.confirm import ConfirmScreen
from asc.screens.context_picker import ContextPickerScreen
from asc.screens.save_confirm import SaveConfirmScreen
from asc.widgets.settings_table import SettingsTable

PROD_ROWS = len(MOCK_DATA[DEFAULT_GROUP][DEFAULT_APP][PRODUCTION])
STAGING_ROWS = len(MOCK_DATA[DEFAULT_GROUP][DEFAULT_APP]["staging"])


async def wait_loaded(pilot) -> None:
    """Wait for all background workers (load / navigate) to finish."""
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
