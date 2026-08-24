"""Edit screen — modal for changing an existing setting's value."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label

from asc.models import compose_kv_ref, parse_kv_ref
from asc.screens.add import parse_kv_input


class EditScreen(ModalScreen[tuple[str, bool] | None]):
    """Modal that lets the user edit the value of an existing setting.

    Dismisses with ``(new_value, slot_setting)`` on save, or None on cancel.
    Pre-checks and pre-fills the Key Vault reference fields when the current
    value already parses as a KV ref.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, key: str, value: str, slot_setting: bool) -> None:
        super().__init__()
        self._key = key
        self._value = value
        self._slot_setting = slot_setting
        self._kv_ref = parse_kv_ref(value)

    def compose(self) -> ComposeResult:
        initial_value = (
            f"{self._kv_ref.vault}/{self._kv_ref.secret}" if self._kv_ref else self._value
        )
        with Vertical(id="edit-container"):
            yield Label(f"Edit  {self._key}", id="edit-title")
            yield Input(value=initial_value, id="edit-value")
            yield Checkbox("Key Vault reference", value=self._kv_ref is not None, id="kv-mode")
            yield Checkbox("deployment slot setting", value=self._slot_setting, id="sticky")
            yield Label("Enter to save · Escape to cancel", id="edit-hint")

    def on_mount(self) -> None:
        input = self.query_one("#edit-value", Input)
        if self._kv_ref is not None:
            input.placeholder = "vault-name/secret-name"
        input.focus()
        input.cursor_position = len(input.value)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "kv-mode":
            return
        input = self.query_one("#edit-value", Input)
        input.placeholder = "vault-name/secret-name" if event.value else ""

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._try_save()

    def _try_save(self) -> None:
        raw = self.query_one("#edit-value", Input).value
        sticky = self.query_one("#sticky", Checkbox).value

        if self.query_one("#kv-mode", Checkbox).value:
            parsed = parse_kv_input(raw)
            if parsed is None:
                self._show_error("Key Vault reference must be 'vault/secret'")
                return
            vault, secret = parsed
            value = compose_kv_ref(vault, secret)
        else:
            value = raw

        self.dismiss((value, sticky))

    def _show_error(self, message: str) -> None:
        hint = self.query_one("#edit-hint", Label)
        hint.update(f"[red]{message}[/]")
        self.set_timer(2.0, lambda: hint.update("Enter to save · Escape to cancel"))

    def action_cancel(self) -> None:
        self.dismiss(None)
