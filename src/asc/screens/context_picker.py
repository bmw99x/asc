"""Context picker modal — select group and app in one step."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option


class ContextPickerScreen(ModalScreen[tuple[str, str] | None]):
    """Modal that lets the user choose a group+app pair.

    Displays every group as a separator followed by its apps as selectable
    options. The currently active group+app pair is pre-highlighted.
    Dismisses with ``(group, app)`` on Enter or ``None`` on Escape/q.
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("q", "cancel", show=False),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
    ]

    DEFAULT_CSS = """
    ContextPickerScreen {
        align: center middle;
    }

    #picker-list {
        width: 36;
        max-height: 20;
        background: $surface-darken-2;
        border: round $primary;
    }

    #picker-title {
        width: 36;
        background: $surface-darken-2;
        border-top: round $primary;
        border-left: round $primary;
        border-right: round $primary;
        padding: 1 1 0 1;
        text-style: bold;
        color: $text;
    }

    #picker-hint {
        width: 36;
        background: $surface-darken-2;
        border-bottom: round $primary;
        border-left: round $primary;
        border-right: round $primary;
        padding: 0 1 1 1;
        color: $text-muted;
    }

    #picker-list:focus {
        border: round $primary;
    }

    #picker-list > .option-list--separator {
        color: $text;
        text-style: bold;
        padding: 0 1;
    }

    #picker-list > .option-list--option {
        padding: 0 1;
        color: $text-muted;
    }

    #picker-list > .option-list--option-highlighted {
        background: $primary;
        color: $text;
        text-style: bold;
    }
    """

    def __init__(
        self,
        groups: dict[str, list[str]],
        current_group: str,
        current_app: str,
    ) -> None:
        super().__init__()
        self._groups = groups
        self._current_group = current_group
        self._current_app = current_app
        # Map option index → (group, app), including disabled items
        self._index_map: list[tuple[str, str] | None] = []

    def compose(self) -> ComposeResult:
        option_list = OptionList(id="picker-list")

        for group, apps in self._groups.items():
            # Add group as separator (disabled, not in index map)
            option_list.add_option(Option(f"  {group}", disabled=True))
            self._index_map.append(None)  # Placeholder for disabled item

            for app in apps:
                is_current = group == self._current_group and app == self._current_app
                self._index_map.append((group, app))

                # Add option with appropriate label
                label = f"  *{app}*" if is_current else f"    {app}"
                option_list.add_option(Option(label, id=f"{group}/{app}"))

        yield Label("  Switch context", id="picker-title")
        yield option_list
        yield Label("  Enter to select · Esc/q to cancel", id="picker-hint")

    def on_mount(self) -> None:
        option_list = self.query_one("#picker-list", OptionList)

        # Find and highlight current app
        for idx, item in enumerate(self._index_map):
            if item is not None:
                group, app = item
                if group == self._current_group and app == self._current_app:
                    option_list.highlighted = idx
                    break

        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle option selection."""
        option_index = event.option_index
        if 0 <= option_index < len(self._index_map):
            item = self._index_map[option_index]
            if item is not None:
                group, app = item
                self.dismiss((group, app))

    def action_cursor_down(self) -> None:
        self.query_one("#picker-list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#picker-list", OptionList).action_cursor_up()

    def action_cancel(self) -> None:
        self.dismiss(None)
