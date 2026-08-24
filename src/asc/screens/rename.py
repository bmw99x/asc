"""Rename screen — modal for renaming a setting's key."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label

from asc.models import key_error


class RenameScreen(ModalScreen[str | None]):
    """Modal that lets the user rename a setting's key.

    Dismisses with the new key string on save, or None on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, old_key: str, existing_keys: set[str]) -> None:
        super().__init__()
        self._old_key = old_key
        self._existing_keys = existing_keys

    def compose(self) -> ComposeResult:
        with Vertical(id="rename-container"):
            yield Label("Rename variable", id="rename-title")
            yield Input(value=self._old_key, id="rename-key")
            yield Label("Enter to save · Escape to cancel", id="rename-hint")

    def on_mount(self) -> None:
        input_widget = self.query_one("#rename-key", Input)
        input_widget.focus()
        input_widget.cursor_position = len(self._old_key)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        new_key = event.value.strip()

        invalid = key_error(new_key)
        if invalid is not None:
            self._show_error(invalid)
            return

        if new_key == self._old_key:
            self.dismiss(None)
            return

        if new_key in self._existing_keys:
            self._show_error(f"Key '{new_key}' already exists")
            return

        self.dismiss(new_key)

    def _show_error(self, message: str) -> None:
        hint = self.query_one("#rename-hint", Label)
        hint.update(f"[red]{message}[/]")
        self.set_timer(2.0, lambda: hint.update("Enter to save · Escape to cancel"))

    def action_cancel(self) -> None:
        self.dismiss(None)
