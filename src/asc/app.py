"""Main application entry point."""

import asyncio
import contextlib
import copy

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.reactive import reactive
from textual.widgets import Footer, Header, Input, LoadingIndicator

from asc.azure.client import AzureClientError
from asc.config import Config, ConfigError, load_config, load_theme, save_theme
from asc.constants import APP_TITLE, DEFAULT_APP, DEFAULT_GROUP, GROUPS, PRODUCTION
from asc.models import Action, ActionKind, AppSetting, KeyVaultRef
from asc.providers import MockProvider, SettingsProvider
from asc.providers_azure import AzureSettingsProvider
from asc.screens.add import AddScreen
from asc.screens.confirm import ConfirmScreen
from asc.screens.context_picker import ContextPickerScreen
from asc.screens.edit import EditScreen
from asc.screens.help import HelpScreen
from asc.screens.rename import RenameScreen
from asc.screens.save_confirm import SaveConfirmScreen
from asc.widgets.main_view import MainView
from asc.widgets.settings_table import SettingsTable
from asc.widgets.slot_tabs import SlotTabs


class AscApp(App):
    """asc — Azure App Service Config TUI.

    Changes are staged locally: every mutation appends an :class:`Action` to
    ``_undo_stack`` and mutates ``_all_settings`` (the working copy) only.
    Nothing reaches the provider until the user confirms a save, at which point
    the working copy is diffed against ``_baseline`` (the snapshot taken when
    the slot loaded) and flushed in a single ``provider.apply`` call.
    """

    # Every widget and screen owns its own stylesheet; app.tcss holds only the
    # app-level chrome. Each rule lives in exactly one file.
    CSS_PATH = [
        "app.tcss",
        "widgets/main_view.tcss",
        "widgets/settings_table.tcss",
        "widgets/slot_tabs.tcss",
        "screens/add.tcss",
        "screens/confirm.tcss",
        "screens/edit.tcss",
        "screens/help.tcss",
        "screens/rename.tcss",
        "screens/save_confirm.tcss",
    ]
    TITLE = APP_TITLE

    dirty: reactive[bool] = reactive(False)
    loading: reactive[bool] = reactive(False)
    current_group: reactive[str] = reactive("")
    current_app: reactive[str] = reactive("")
    current_slot: reactive[str] = reactive("")

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("?", "toggle_help", "Help"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", show=False),
        Binding("g", "jump_top", show=False),
        Binding("G", "jump_bottom", show=False),
        Binding("y", "copy_value", "Copy"),
        Binding("Y", "copy_resolved", "Copy resolved"),
        Binding("i", "edit_setting", "Edit"),
        Binding("enter", "edit_setting", show=False),
        Binding("r", "rename_setting", "Rename"),
        Binding("o", "add_setting", "Add"),
        Binding("d", "delete_setting", "dd Delete"),
        Binding("t", "toggle_sticky", "Sticky"),
        Binding("u", "undo", "Undo"),
        Binding("s", "save_changes", "Save"),
        Binding("p", "pick_context", "Group"),
        Binding("e", "cycle_slot_next", "Slot"),
        Binding("tab", "cycle_slot_next", show=False),
    ]

    def __init__(
        self,
        provider: SettingsProvider | None = None,
        _use_config: bool = False,
    ) -> None:
        super().__init__()
        self._config: Config = {}
        if _use_config:
            with contextlib.suppress(ConfigError):
                self._config = load_config()
        if not _use_config or not self._config:
            self._groups: dict[str, list[str]] = GROUPS
            default_group = DEFAULT_GROUP
            default_app = DEFAULT_APP
            self._using_mock = True
        else:
            self._groups = {group: list(apps) for group, apps in self._config.items()}
            default_group = next(iter(self._groups))
            default_app = self._groups[default_group][0]
            self._using_mock = False

        self._default_group = default_group
        self._default_app = default_app
        self._provider_injected: bool = provider is not None
        self._provider: SettingsProvider = provider or MockProvider(default_group, default_app)
        # _all_settings is the local working copy (includes uncommitted staged
        # changes); _baseline is what the provider held when the slot loaded.
        self._all_settings: list[AppSetting] = []
        self._baseline: list[AppSetting] = []
        # Slots are discovered from the provider, not from config.
        self._slots: list[str] = [PRODUCTION]
        self._filter: str = ""
        self._g_pressed: bool = False
        self._d_pressed: bool = False
        # _undo_stack holds staged (uncommitted) changes.
        self._undo_stack: list[Action] = []
        # Last app used per group, so returning to a group lands somewhere sane.
        self._group_app_memory: dict[str, str] = {
            group: apps[0] for group, apps in self._groups.items() if apps
        }

    def compose(self) -> ComposeResult:
        yield Header()
        yield SlotTabs(id="slot-tabs")
        yield MainView(id="main")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search", Input).display = False
        self.query_one("#loading", LoadingIndicator).display = False
        saved_theme = load_theme()
        if saved_theme:
            self.theme = saved_theme
        self._load_initial()

    # ------------------------------------------------------------------ load

    @work
    async def _load_initial(self) -> None:
        """Discover slots and load the default group/app on startup."""
        self.loading = True
        await self._load_context(
            self._default_group, self._default_app, PRODUCTION, refetch_slots=True
        )
        self.loading = False
        self._get_table().focus()

    async def _load_context(
        self, group: str, app: str, slot: str, *, refetch_slots: bool
    ) -> None:
        """Point the provider at *group*/*app* and load *slot*'s settings.

        Any staged changes are dropped — callers are expected to have asked the
        user first (see :meth:`_confirm_navigate`).
        """
        app = self._resolve_app(group, app)
        if not self._provider_injected:
            provider = self._make_provider(group, app)
            if provider is None:
                return
            self._provider = provider

        self.current_group = group
        self.current_app = app

        if refetch_slots:
            self._slots = self._discover_slots()
        if slot not in self._slots:
            slot = self._slots[0]
        self.current_slot = slot

        self._all_settings = self._fetch_settings(slot)
        self._baseline = copy.deepcopy(self._all_settings)
        self._undo_stack.clear()
        self.dirty = False
        self._refresh_table()
        await self.query_one("#slot-tabs", SlotTabs).update_slots(app, self._slots, slot)
        self._update_subtitle()

    def _resolve_app(self, group: str, app: str) -> str:
        """Return *app* if it belongs to *group*, else the remembered one."""
        apps = self._groups.get(group, [])
        if app in apps:
            return app
        remembered = self._group_app_memory.get(group)
        if remembered in apps:
            return str(remembered)
        return apps[0] if apps else app

    def _make_provider(self, group: str, app: str) -> SettingsProvider | None:
        """Build a provider for *group*/*app*, or None if config is missing."""
        if self._using_mock:
            return MockProvider(group, app)
        try:
            return AzureSettingsProvider(self._config[group][app])
        except KeyError as exc:
            self.notify(f"Config missing {group}/{app}: {exc}", severity="error", timeout=8)
            return None

    def _discover_slots(self) -> list[str]:
        """Fetch the provider's slot list, falling back to production only."""
        try:
            slots = self._provider.list_slots()
        except AzureClientError as exc:
            self.notify(f"Slot discovery failed: {exc}", severity="warning", timeout=8)
            return [PRODUCTION]
        return slots or [PRODUCTION]

    def _fetch_settings(self, slot: str) -> list[AppSetting]:
        """Fetch settings for *slot*, returning an empty list on failure."""
        try:
            return self._provider.list_settings(slot)
        except AzureClientError as exc:
            self.notify(f"Load failed: {exc}", severity="error", timeout=8)
            return []

    # --------------------------------------------------------------- display

    def _update_subtitle(self) -> None:
        backend = "mock" if self._using_mock else "Azure App Service"
        base = f"[{self.current_group} · {self.current_app} · {self.current_slot}] · {backend}"
        n = len(self._undo_stack)
        if n == 1:
            self.sub_title = f"{base}  1 unsaved change"
        elif n > 1:
            self.sub_title = f"{base}  {n} unsaved changes"
        else:
            self.sub_title = base

    def watch_dirty(self, dirty: bool) -> None:
        """Reflect unsaved state in the subtitle."""
        self._update_subtitle()

    def watch_loading(self, loading: bool) -> None:
        """Show or hide the loading overlay."""
        indicator = self.query_one("#loading", LoadingIndicator)
        indicator.display = loading
        self.query_one("#env-table", SettingsTable).display = not loading

    def watch_theme(self, theme: str) -> None:
        """Persist theme changes whenever the theme is changed."""
        save_theme(theme)

    def watch_current_group(self, group: str) -> None:
        """Keep the subtitle in step with the active group."""
        self._update_subtitle()

    def watch_current_app(self, app: str) -> None:
        """Keep the subtitle in step with the active app."""
        self._update_subtitle()

    def watch_current_slot(self, slot: str) -> None:
        """Keep the subtitle in step with the active slot."""
        self._update_subtitle()

    def _get_table(self) -> SettingsTable:
        return self.query_one("#env-table", SettingsTable)

    def _refresh_table(self) -> None:
        """Repopulate the table, applying the current filter if any."""
        settings = (
            [s for s in self._all_settings if s.matches(self._filter)]
            if self._filter
            else self._all_settings
        )
        self._get_table().set_rows(settings)

    def _selected_key(self) -> str | None:
        """Return the key of the currently highlighted table row, or None."""
        return self._get_table().selected_key

    def _find(self, key: str) -> AppSetting | None:
        """Return the working-copy setting for *key*, or None."""
        return next((s for s in self._all_settings if s.key == key), None)

    # --------------------------------------------------------------- staging

    def _stage_set(self, key: str, value: str, slot_setting: bool = False) -> None:
        """Stage a set (add or edit) in the local working copy.

        The provider is NOT written here — see :meth:`_commit_staged`.
        """
        existing = self._find(key)
        previous_value = existing.value if existing is not None else None
        previous_slot_setting = existing.slot_setting if existing is not None else False

        if existing is None:
            self._all_settings.append(AppSetting(key=key, value=value, slot_setting=slot_setting))
        else:
            existing.value = value
            existing.slot_setting = slot_setting

        self._undo_stack.append(
            Action(
                kind=ActionKind.SET,
                key=key,
                value=value,
                previous_value=previous_value,
                slot_setting=slot_setting,
                previous_slot_setting=previous_slot_setting,
            )
        )
        self.dirty = True
        self._refresh_table()
        self._update_subtitle()

    def _stage_sticky(self, key: str) -> None:
        """Stage a flip of *key*'s deployment-slot-setting flag."""
        setting = self._find(key)
        if setting is None:
            return
        setting.slot_setting = not setting.slot_setting
        self._undo_stack.append(
            Action(
                kind=ActionKind.TOGGLE_STICKY,
                key=key,
                value=setting.value,
                slot_setting=setting.slot_setting,
                previous_slot_setting=not setting.slot_setting,
            )
        )
        self.dirty = True
        self._refresh_table()
        self._update_subtitle()

    def _stage_delete(self, key: str) -> None:
        """Stage a delete in the local working copy.

        If an existing staged action already covers this key the operations are
        collapsed so the undo stack (and the save diff) stay honest:

        - Staged ADD (previous_value=None): cancel the add — net zero.
        - Staged EDIT (previous_value=<str>): drop the edit, push a DELETE of
          the original value/flag.
        - Staged RENAME (old_key → key): drop the rename, push a DELETE of the
          original key.
        """
        existing = self._find(key)
        if existing is None:
            return

        current_value = existing.value
        current_slot_setting = existing.slot_setting

        prior = next((a for a in reversed(self._undo_stack) if a.key == key), None)

        self._all_settings = [s for s in self._all_settings if s.key != key]

        if prior is not None and prior.kind == ActionKind.SET and prior.previous_value is None:
            # Was a staged add that never existed in the provider — cancel it out.
            self._undo_stack.remove(prior)
        elif (
            prior is not None and prior.kind == ActionKind.SET and prior.previous_value is not None
        ):
            # Was a staged edit — replace with a delete of the original.
            self._undo_stack.remove(prior)
            self._undo_stack.append(
                Action(
                    kind=ActionKind.DELETE,
                    key=key,
                    value=prior.previous_value,
                    slot_setting=prior.previous_slot_setting,
                )
            )
        elif prior is not None and prior.kind == ActionKind.RENAME and prior.old_key is not None:
            # Was a staged rename — replace with a delete of the original key.
            self._undo_stack.remove(prior)
            self._undo_stack.append(
                Action(
                    kind=ActionKind.DELETE,
                    key=prior.old_key,
                    value=prior.value,
                    slot_setting=prior.slot_setting,
                )
            )
        else:
            self._undo_stack.append(
                Action(
                    kind=ActionKind.DELETE,
                    key=key,
                    value=current_value,
                    slot_setting=current_slot_setting,
                )
            )

        self.dirty = bool(self._undo_stack)
        self._refresh_table()
        self._update_subtitle()

    def _stage_rename(self, old_key: str, new_key: str) -> None:
        """Stage a rename in the local working copy."""
        setting = self._find(old_key)
        if setting is None:
            return
        setting.key = new_key
        self._undo_stack.append(
            Action(
                kind=ActionKind.RENAME,
                key=new_key,
                value=setting.value,
                old_key=old_key,
                slot_setting=setting.slot_setting,
            )
        )
        self.dirty = True
        self._refresh_table()
        self._update_subtitle()

    # ------------------------------------------------------------------ save

    def _net_changes(self) -> tuple[list[AppSetting], list[str]]:
        """Collapse the staged working copy into (upserts, deletes).

        The diff is taken against ``_baseline`` — the snapshot captured when
        the slot loaded — so renames fall out naturally (old key in deletes,
        new key in upserts) and edit-then-undo collapses to nothing.
        """
        baseline = {s.key: s for s in self._baseline}
        current = {s.key: s for s in self._all_settings}
        upserts = [
            s
            for key, s in current.items()
            if key not in baseline
            or baseline[key].value != s.value
            or baseline[key].slot_setting != s.slot_setting
        ]
        deletes = [key for key in baseline if key not in current]
        return upserts, deletes

    def _commit_staged(self) -> None:
        """Flush the net diff to the provider in a single apply call.

        On failure the stage and undo stack are left intact so the user can
        retry; on success the baseline is re-captured.
        """
        upserts, deletes = self._net_changes()
        if not upserts and not deletes:
            self._undo_stack.clear()
            self.dirty = False
            self._update_subtitle()
            self.notify("Nothing to save", timeout=2)
            return

        try:
            self._provider.apply(self.current_slot, upserts, deletes)
        except AzureClientError as exc:
            self.notify(f"Save failed: {exc}", severity="error", timeout=8)
            return

        self._undo_stack.clear()
        self._baseline = copy.deepcopy(self._all_settings)
        self.dirty = False
        self._update_subtitle()
        n = len(upserts) + len(deletes)
        self.notify(f"Saved {n} change{'' if n == 1 else 's'}", timeout=2)

    # ------------------------------------------------------------ navigation

    @work
    async def _navigate_to(self, group: str, app: str, slot: str) -> None:
        """Switch to group/app/slot unconditionally, dropping staged changes."""
        if self.current_group:
            self._group_app_memory[self.current_group] = self.current_app
        refetch_slots = group != self.current_group or app != self.current_app
        self.loading = True
        await asyncio.sleep(0.4)
        await self._load_context(group, app, slot, refetch_slots=refetch_slots)
        self.loading = False
        self._get_table().focus()

    def _confirm_navigate(self, group: str, app: str, slot: str) -> None:
        """Navigate to group/app/slot, prompting if there are unsaved changes."""
        if not self.dirty:
            self._navigate_to(group, app, slot)
            return

        n = len(self._undo_stack)
        noun = "change" if n == 1 else "changes"
        msg = f"You have {n} unsaved {noun}. Switch anyway?"

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._navigate_to(group, app, slot)
            else:
                self._get_table().focus()

        self.push_screen(ConfirmScreen(msg), on_confirm)

    def action_cycle_slot_next(self) -> None:
        """Advance to the next slot for the current app (wraps around)."""
        if not self._slots:
            return
        idx = self._slots.index(self.current_slot) if self.current_slot in self._slots else 0
        next_slot = self._slots[(idx + 1) % len(self._slots)]
        if next_slot == self.current_slot:
            return
        self._confirm_navigate(self.current_group, self.current_app, next_slot)

    def action_pick_context(self) -> None:
        """Open the unified group+app picker modal."""

        def on_pick(result: tuple[str, str] | None) -> None:
            if result is None:
                self._get_table().focus()
                return
            group, app = result
            if group == self.current_group and app == self.current_app:
                self._get_table().focus()
                return
            # Switching app resets the slot — slot names are per-app.
            self._confirm_navigate(group, app, PRODUCTION)

        self.push_screen(
            ContextPickerScreen(self._groups, self.current_group, self.current_app),
            on_pick,
        )

    def on_slot_tabs_tab_clicked(self, event: SlotTabs.TabClicked) -> None:
        """Handle a tab click — same confirm-navigate flow as pressing e."""
        event.stop()
        if event.slot == self.current_slot:
            return
        self._confirm_navigate(self.current_group, self.current_app, event.slot)

    def on_slot_tabs_app_clicked(self, event: SlotTabs.AppClicked) -> None:
        """Open the context picker when the app label is clicked."""
        event.stop()
        self.action_pick_context()

    def on_settings_table_row_double_clicked(
        self, event: SettingsTable.RowDoubleClicked
    ) -> None:
        """Open the edit modal on double-click."""
        event.stop()
        self.action_edit_setting()

    # --------------------------------------------------------------- actions

    def action_toggle_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_focus_search(self) -> None:
        """Show and focus the search bar."""
        search = self.query_one("#search", Input)
        search.display = True
        search.focus()

    def action_clear_search(self) -> None:
        """Clear active filter and hide the search bar."""
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
            self._filter = ""
            self._refresh_table()
        search.display = False
        self._get_table().focus()

    def action_jump_top(self) -> None:
        """Implement vim-style gg: move to the first row on the second g press."""
        if self._g_pressed:
            self._g_pressed = False
            self._get_table().move_cursor(row=0)
        else:
            self._g_pressed = True
            self.set_timer(0.5, self._reset_g)

    def _reset_g(self) -> None:
        self._g_pressed = False

    def action_jump_bottom(self) -> None:
        """Move cursor to the last row (vim G)."""
        table = self._get_table()
        table.move_cursor(row=table.row_count - 1)

    def action_copy_value(self) -> None:
        """Copy the selected row's value — Key Vault rows copy the raw reference."""
        setting = self._find(self._selected_key() or "")
        if setting is None:
            return
        self.copy_to_clipboard(setting.value)
        msg = (
            "Copied Key Vault reference to clipboard"
            if setting.kv_ref is not None
            else "Copied value to clipboard"
        )
        self.notify(msg, timeout=2)

    def action_copy_resolved(self) -> None:
        """Copy the resolved secret behind the selected Key Vault reference."""
        setting = self._find(self._selected_key() or "")
        if setting is None or setting.kv_ref is None:
            self.notify("Not a Key Vault reference", severity="warning", timeout=2)
            return
        self._resolve_and_copy(setting.kv_ref)

    @work(thread=True)
    def _resolve_and_copy(self, ref: KeyVaultRef) -> None:
        """Resolve *ref* off the event loop, then copy it on the main thread."""
        try:
            value = self._provider.resolve_kv(ref)
        except AzureClientError as exc:
            # Bind the message here: exc is unbound once the except block ends.
            message = f"Resolve failed: {exc}"
            self.call_from_thread(lambda: self.notify(message, severity="error", timeout=8))
            return

        def copy() -> None:
            self.copy_to_clipboard(value)
            self.notify("Copied resolved value to clipboard", timeout=2)

        self.call_from_thread(copy)

    def action_edit_setting(self) -> None:
        """Open the edit modal for the currently selected setting."""
        key = self._selected_key()
        if key is None:
            return
        setting = self._find(key)
        if setting is None:
            return
        current_value = setting.value
        current_sticky = setting.slot_setting

        def on_save(result: tuple[str, bool] | None) -> None:
            if result is not None:
                value, sticky = result
                if value != current_value:
                    # A value change carries the flag along in one SET.
                    self._stage_set(key, value, sticky)
                    self.notify(f"Staged update to {key}", timeout=2)
                elif sticky != current_sticky:
                    # Only the checkbox moved — that is a sticky toggle.
                    self._stage_sticky(key)
                    self.notify(f"Staged slot setting for {key}", timeout=2)
            self._get_table().focus()

        self.push_screen(
            EditScreen(key=key, value=current_value, slot_setting=current_sticky), on_save
        )

    def action_rename_setting(self) -> None:
        """Open the rename modal for the selected setting's key."""
        key = self._selected_key()
        if key is None:
            return
        existing = {s.key for s in self._all_settings}

        def on_rename(new_key: str | None) -> None:
            if new_key is not None:
                self._stage_rename(key, new_key)
                self.notify(f"Staged rename {key} → {new_key}", timeout=2)
            self._get_table().focus()

        self.push_screen(RenameScreen(old_key=key, existing_keys=existing), on_rename)

    def action_add_setting(self) -> None:
        """Open the add modal to insert a new setting."""
        existing = {s.key for s in self._all_settings}

        def on_save(result: tuple[str, str, bool] | None) -> None:
            if result is not None:
                key, value, sticky = result
                self._stage_set(key, value, sticky)
                self.notify(f"Staged add {key}", timeout=2)
            self._get_table().focus()

        self.push_screen(AddScreen(existing_keys=existing), on_save)

    def action_toggle_sticky(self) -> None:
        """Stage a flip of the selected setting's deployment-slot flag."""
        key = self._selected_key()
        if key is None:
            return
        setting = self._find(key)
        if setting is None:
            return
        self._stage_sticky(key)
        state = "on" if setting.slot_setting else "off"
        self.notify(f"Staged slot setting {state} for {key}", timeout=2)

    def action_delete_setting(self) -> None:
        """Implement vim-style dd: stage deletion of the selected setting."""
        if self._d_pressed:
            self._d_pressed = False
            key = self._selected_key()
            if key is None:
                return
            self._stage_delete(key)
            self.notify(f"Staged delete {key}", timeout=2)
            self._get_table().focus()
        else:
            self._d_pressed = True
            self.set_timer(0.5, self._reset_d)

    def _reset_d(self) -> None:
        self._d_pressed = False

    def action_save_changes(self) -> None:
        """Open SaveConfirmScreen to review and commit staged changes."""
        if not self._undo_stack:
            self.notify("No unsaved changes", timeout=2)
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._commit_staged()
            self._get_table().focus()

        self.push_screen(SaveConfirmScreen(list(self._undo_stack)), on_confirm)

    def action_undo(self) -> None:
        """Reverse the most recent staged mutation."""
        if not self._undo_stack:
            self.notify("Nothing to undo", timeout=2)
            return

        action = self._undo_stack.pop()

        if action.kind == ActionKind.SET:
            if action.previous_value is None:
                # Was an add — remove from the working copy.
                self._all_settings = [s for s in self._all_settings if s.key != action.key]
            else:
                setting = self._find(action.key)
                if setting is not None:
                    setting.value = action.previous_value
                    setting.slot_setting = action.previous_slot_setting
        elif action.kind == ActionKind.DELETE:
            self._all_settings.append(
                AppSetting(
                    key=action.key, value=action.value, slot_setting=action.slot_setting
                )
            )
        elif action.kind == ActionKind.RENAME and action.old_key is not None:
            setting = self._find(action.key)
            if setting is not None:
                setting.key = action.old_key
        elif action.kind == ActionKind.TOGGLE_STICKY:
            setting = self._find(action.key)
            if setting is not None:
                setting.slot_setting = action.previous_slot_setting

        self.dirty = bool(self._undo_stack)
        self._refresh_table()
        self._update_subtitle()
        self.notify(f"Undid change to {action.key}", timeout=2)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter = event.value
            self._refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self._get_table().focus()


def main() -> None:
    AscApp(_use_config=True).run()


if __name__ == "__main__":
    main()
