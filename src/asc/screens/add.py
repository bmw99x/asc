"""Add screen — modal for inserting a new app setting."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Checkbox, Input, Label

from asc.models import compose_kv_ref, key_error


def parse_kv_input(text: str) -> tuple[str, str] | None:
    """Split ``vault/secret`` into ``(vault, secret)``; None if malformed."""
    if "/" not in text:
        return None
    vault, _, secret = text.partition("/")
    vault = vault.strip()
    secret = secret.strip()
    if not vault or not secret:
        return None
    return (vault, secret)


class AddScreen(ModalScreen[tuple[str, str, bool] | None]):
    """Modal that lets the user add a new key/value setting.

    Dismisses with ``(key, value, slot_setting)`` on save, or None on cancel.
    Inline validation prevents duplicate, blank or ``-``-prefixed keys, and
    malformed Key Vault references when "Key Vault reference" is checked.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    def __init__(self, existing_keys: set[str]) -> None:
        super().__init__()
        self._existing_keys = existing_keys

    def compose(self) -> ComposeResult:
        with Vertical(id="add-container"):
            yield Label("Add variable", id="add-title")
            yield Input(placeholder="KEY", id="add-key")
            yield Input(placeholder="value", id="add-value")
            yield Checkbox("Key Vault reference", id="kv-mode")
            yield Checkbox("deployment slot setting", id="sticky")
            yield Label("", id="add-error")
            yield Label("Tab · Enter to save · Escape to cancel", id="add-hint")

    def on_mount(self) -> None:
        self.query_one("#add-key", Input).focus()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id != "kv-mode":
            return
        value_input = self.query_one("#add-value", Input)
        value_input.placeholder = "vault-name/secret-name" if event.value else "value"

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "add-key":
            self.query_one("#add-value", Input).focus()
            return
        if event.input.id == "add-value":
            self._try_save()

    def _try_save(self) -> None:
        key = self.query_one("#add-key", Input).value.strip()
        error = self.query_one("#add-error", Label)

        invalid = key_error(key)
        if invalid is not None:
            error.update(invalid)
            self.query_one("#add-key", Input).focus()
            return

        if key in self._existing_keys:
            error.update(f"'{key}' already exists — use edit instead")
            self.query_one("#add-key", Input).focus()
            return

        sticky = self.query_one("#sticky", Checkbox).value
        raw_value = self.query_one("#add-value", Input).value

        if self.query_one("#kv-mode", Checkbox).value:
            parsed = parse_kv_input(raw_value)
            if parsed is None:
                error.update("Key Vault reference must be 'vault/secret'")
                self.query_one("#add-value", Input).focus()
                return
            vault, secret = parsed
            value = compose_kv_ref(vault, secret)
        else:
            value = raw_value

        self.dismiss((key, value, sticky))

    def action_cancel(self) -> None:
        self.dismiss(None)
