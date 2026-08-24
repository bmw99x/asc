"""Save-confirm screen — shows a coloured diff of pending changes before writing."""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label

from asc.models import Action, ActionKind


def diff_line(action: Action) -> Text:
    """Build a single coloured diff line for one staged action.

    + Added settings in green
    - Removed settings in red
    ~ Renamed settings (old → new) in yellow
    * Edited settings in blue
    ~ Toggled slot-setting flag in magenta
    SET lines get a trailing " [slot]" when the action's slot_setting is True.
    """
    if action.kind == ActionKind.RENAME:
        old = action.old_key or "?"
        return Text(f"~  {old}  →  {action.key}", style="yellow")
    if action.kind == ActionKind.DELETE:
        return Text(f"-  {action.key}", style="red")
    if action.kind == ActionKind.TOGGLE_STICKY:
        state = "on" if action.slot_setting else "off"
        return Text(f"~  {action.key}  slot setting → {state}", style="magenta")

    suffix = " [slot]" if action.slot_setting else ""
    if action.previous_value is None:
        return Text(f"+  {action.key}{suffix}", style="green")
    return Text(f"*  {action.key}{suffix}", style="blue")


class SaveConfirmScreen(ModalScreen[bool]):
    """Modal showing a coloured diff of staged changes.

    Dismisses True on confirm, False on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("n", "cancel", show=False),
        Binding("q", "cancel", show=False),
        Binding("y", "confirm", show=False),
        Binding("h", "focus_yes", show=False),
        Binding("left", "focus_yes", show=False),
        Binding("l", "focus_no", show=False),
        Binding("right", "focus_no", show=False),
    ]

    def __init__(self, actions: list[Action]) -> None:
        super().__init__()
        self._actions = actions

    def compose(self) -> ComposeResult:
        with Vertical(id="save-confirm-container"):
            yield Label("Save changes?", id="save-confirm-title")
            with ScrollableContainer(id="save-confirm-diff"):
                if self._actions:
                    for action in self._actions:
                        yield Label(diff_line(action))
                else:
                    yield Label(Text("(no changes)", style="dim"))
            with Horizontal(id="save-confirm-buttons"):
                yield Button("Save", variant="success", id="save-confirm-yes")
                yield Button("Cancel", variant="primary", id="save-confirm-no")

    def on_mount(self) -> None:
        self.query_one("#save-confirm-no", Button).focus()

    def has_change(self, key: str) -> bool:
        """Return True if *key* appears in any staged action (for tests)."""
        for action in self._actions:
            if action.key == key:
                return True
            if action.kind == ActionKind.RENAME and action.old_key == key:
                return True
        return False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "save-confirm-yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_focus_yes(self) -> None:
        self.query_one("#save-confirm-yes", Button).focus()

    def action_focus_no(self) -> None:
        self.query_one("#save-confirm-no", Button).focus()
