"""Help overlay screen."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Static

from asc.constants import HELP_TEXT


class HelpScreen(ModalScreen):
    """Modal overlay displaying keyboard shortcuts."""

    BINDINGS = [
        Binding("escape", "dismiss", show=False),
        Binding("?", "dismiss", show=False),
        Binding("q", "dismiss", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Container(
            Static(HELP_TEXT, id="help-text"),
            id="help-container",
        )

    def on_click(self) -> None:
        """Dismiss on any click outside the help box."""
        self.dismiss()
