"""Confirm screen — reusable yes/no modal."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class ConfirmScreen(ModalScreen[bool]):
    """Modal that asks the user to confirm or cancel a destructive action.

    Dismisses with True on confirm, False on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("y", "confirm", show=False),
        Binding("n", "cancel", show=False),
        Binding("q", "cancel", show=False),
        Binding("h", "focus_yes", show=False),
        Binding("left", "focus_yes", show=False),
        Binding("l", "focus_no", show=False),
        Binding("right", "focus_no", show=False),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-container"):
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", variant="error", id="confirm-yes")
                yield Button("No", variant="primary", id="confirm-no")

    def on_mount(self) -> None:
        self.query_one("#confirm-no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_yes(self) -> None:
        self.query_one("#confirm-yes", Button).focus()

    def action_focus_no(self) -> None:
        self.query_one("#confirm-no", Button).focus()
