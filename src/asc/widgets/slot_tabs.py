"""Horizontal deployment-slot indicator bar with app label (read-only)."""

import re

from textual.app import ComposeResult
from textual.events import Click
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static


def _tab_id(slot: str) -> str:
    """Return a valid Textual widget ID for a slot name.

    Replaces any character that is not a letter, digit, underscore, or hyphen
    with a hyphen, then collapses consecutive hyphens.
    """
    slug = re.sub(r"[^A-Za-z0-9_-]", "-", slot)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return f"tab-{slug}"


class SlotTabs(Widget):
    """A read-only bar showing the active app and its deployment slots.

    Renders as:  api ▸  production  [staging]

    Unlike kvt's ``EnvTabs`` (which knew every project's environments up
    front from a static ``PROJECTS`` map, and tracked the active project/env
    via reactives), asc's slots are fetched dynamically per app from Azure.
    There is no equivalent to kvt's constructor-supplied ``projects`` map or
    its ``current_project``/``current_env`` reactives; instead the app calls
    ``update_slots()`` whenever the current app or its slots change.

    Clicking a slot tab posts ``SlotTabs.TabClicked`` for the app to handle.
    Clicking the app label posts ``SlotTabs.AppClicked``.
    """

    class TabClicked(Message):
        """Posted when the user clicks a slot tab."""

        def __init__(self, slot: str) -> None:
            super().__init__()
            self.slot = slot

    class AppClicked(Message):
        """Posted when the user clicks the app label."""

    can_focus = False
    BINDINGS = []

    def __init__(self, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(id=id, classes=classes)
        self._app_label = ""
        self._slots: list[str] = []
        self._active = ""

    def _make_tab(self, slot: str, active: bool) -> Static:
        """Create a tab Static with the slot stored in a data attribute."""
        tab = Static(slot, id=_tab_id(slot), classes="tab active" if active else "tab")
        tab.data_slot = slot  # ty: ignore[unresolved-attribute]
        return tab

    def compose(self) -> ComposeResult:
        yield Static(f"{self._app_label} ▸", id="slot-tabs-app", classes="tab-project")
        for slot in self._slots:
            yield self._make_tab(slot, active=slot == self._active)

    async def update_slots(self, app_label: str, slots: list[str], active: str) -> None:
        """Rebuild the bar for a new app label, slot list, and active slot."""
        self._app_label = app_label
        self._slots = slots
        self._active = active

        if not self.is_mounted:
            return

        self.query_one("#slot-tabs-app", Static).update(f"{app_label} ▸")

        # Await removal so the DOM is clean before mounting new tabs.
        await self.query(".tab").remove()
        await self.mount(*[self._make_tab(slot, active=slot == active) for slot in slots])

    def on_click(self, event: Click) -> None:
        """Handle clicks on the app label and slot tabs."""
        widget = event.widget
        if widget is None:
            return
        if widget.id == "slot-tabs-app":
            self.post_message(SlotTabs.AppClicked())
            return
        if widget.has_class("tab") and widget.id:
            slot: str | None = getattr(widget, "data_slot", None)
            if slot is not None:
                self.post_message(SlotTabs.TabClicked(slot))
