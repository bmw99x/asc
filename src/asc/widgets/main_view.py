"""Main view: search bar stacked above the app settings table."""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Input, LoadingIndicator

from asc.widgets.settings_table import SettingsTable


class MainView(Vertical):
    """Composes the search input and the settings table into a single panel."""

    def compose(self) -> ComposeResult:
        yield Input(placeholder="Search keys and values…", id="search")
        yield SettingsTable(id="env-table")
        yield LoadingIndicator(id="loading")
