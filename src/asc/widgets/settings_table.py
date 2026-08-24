"""Settings table widget."""

from rich.text import Text
from textual.binding import Binding
from textual.events import Click
from textual.message import Message
from textual.widgets import DataTable

from asc.constants import TABLE_COLUMNS
from asc.models import AppSetting, is_kv_ref


def badge_text(s: AppSetting) -> Text:
    """Build the badge cell text for a setting.

    ``[KV]`` (cyan) is shown when the value is a Key Vault reference, and
    ``[slot]`` (yellow) is shown when the setting is slot-sticky. Either,
    both, or neither may be present.
    """
    text = Text()
    if is_kv_ref(s.value):
        text.append("[KV]", style="cyan")
    if s.slot_setting:
        if text.plain:
            text.append(" ")
        text.append("[slot]", style="yellow")
    return text


def value_text(s: AppSetting) -> Text:
    """Build the value cell text for a setting.

    Key Vault references are rendered as ``vault/secret`` in dim italic
    instead of the raw ``@Microsoft.KeyVault(...)`` blob.
    """
    ref = s.kv_ref
    if ref is not None:
        return Text(f"{ref.vault}/{ref.secret}", style="dim italic")
    return Text(s.value)


class SettingsTable(DataTable):
    """Scrollable table of app settings with vim-style navigation.

    Rows are keyed by the setting's key name, making it safe to repopulate
    without losing the cursor position across refreshes.

    Key Vault references are rendered with a dim italic ``vault/secret``
    value and a cyan ``[KV]`` badge; slot-sticky settings additionally get
    a yellow ``[slot]`` badge, so both distinctions are immediately visible.

    Double-clicking a row posts ``SettingsTable.RowDoubleClicked`` so the
    app can open the appropriate edit modal without any keyboard
    interaction.
    """

    class RowDoubleClicked(Message):
        """Posted when the user double-clicks a row."""

    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    def action_cursor_down(self) -> None:
        """Move down one row, wrapping from the last row to the first."""
        if self.row_count == 0:
            return
        if self.cursor_row == self.row_count - 1:
            self.move_cursor(row=0)
        else:
            super().action_cursor_down()

    def action_cursor_up(self) -> None:
        """Move up one row, wrapping from the first row to the last."""
        if self.row_count == 0:
            return
        if self.cursor_row == 0:
            self.move_cursor(row=self.row_count - 1)
        else:
            super().action_cursor_up()

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(*TABLE_COLUMNS)

    def set_rows(self, settings: list[AppSetting]) -> None:
        """Replace table contents with a new list of settings."""
        self.clear()
        for s in settings:
            self.add_row(s.key, value_text(s), badge_text(s), key=s.key)

    @property
    def selected_key(self) -> str | None:
        """Return the key of the currently highlighted row, or None."""
        if self.row_count == 0:
            return None
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        return str(row_key.value) if row_key.value is not None else None

    def on_click(self, event: Click) -> None:
        """Post RowDoubleClicked on a double-click (chain == 2)."""
        if event.chain == 2 and self.row_count > 0:
            self.post_message(SettingsTable.RowDoubleClicked())
