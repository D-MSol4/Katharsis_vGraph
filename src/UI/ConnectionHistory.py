from datetime import datetime

from gi.repository import Adw, Gtk, GLib

from Data.Container import Container
from Messaging.Broker import Broker
from Messaging.Events import (
    ContainerConnect, WipeFinish, LabStartFinish, ContainersUpdate
)


class ConnectionHistory(Gtk.Box):
    """Sidebar widget showing the last N connections, newest first.

    Clicking an entry re-connects to that container.
    """

    MAX_ENTRIES = 6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs,
                         orientation=Gtk.Orientation.VERTICAL,
                         spacing=0)

        # Track (container, timestamp) in order of connection
        self._history: list[tuple[Container, datetime]] = []
        # Map container -> its ActionRow (for deduplication / update)
        self._rows: dict[Container, Adw.ActionRow] = {}
        # Keep live containers so history can check if they're still valid
        self._live_containers: set[Container] = set()

        # Header
        header = Gtk.Label(
            label="Recent connections",
            halign=Gtk.Align.START,
            margin_start=16,
            margin_top=12,
            margin_bottom=4,
        )
        header.add_css_class("caption-heading")
        self.append(header)

        # Preferences group acts as a styled list container
        self._group = Adw.PreferencesGroup()
        self.append(self._group)

        Broker.subscribe(ContainerConnect, self._on_connect)
        Broker.subscribe(WipeFinish, self._on_clear)
        Broker.subscribe(LabStartFinish, self._on_clear)
        Broker.subscribe(ContainersUpdate, self._on_containers_update)

    # ------------------------------------------------------------------ #
    # Event handlers
    # ------------------------------------------------------------------ #

    def _on_containers_update(self, event: ContainersUpdate):
        self._live_containers = set(event.containers)

    def _on_connect(self, event: ContainerConnect):
        container = event.container
        now = datetime.now()

        # Remove existing entry for the same container (will be re-added at top)
        if container in self._rows:
            self._group.remove(self._rows.pop(container))
            self._history = [(c, t) for c, t in self._history if c != container]

        # Prepend
        self._history.insert(0, (container, now))

        # Enforce max
        while len(self._history) > self.MAX_ENTRIES:
            old_container, _ = self._history.pop()
            if old_container in self._rows:
                self._group.remove(self._rows.pop(old_container))

        # Build row
        row = self._build_row(container, now)
        self._rows[container] = row
        # Insert at position 0 (top of the group)
        # Adw.PreferencesGroup has no insert-at API; rebuild all rows in order
        self._rebuild_group()

    def _on_clear(self, _):
        for row in self._rows.values():
            self._group.remove(row)
        self._rows.clear()
        self._history.clear()

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _build_row(self, container: Container, ts: datetime) -> Adw.ActionRow:
        time_str = ts.strftime("%H:%M:%S")
        row = Adw.ActionRow(
            title=container.name,
            subtitle=f"Connected at {time_str}",
            activatable=True,
        )
        row.add_prefix(Gtk.Image(icon_name="network-server-symbolic", pixel_size=14))
        row.connect("activated", lambda _r: Broker.notify(ContainerConnect(container)))
        return row

    def _rebuild_group(self):
        """Re-insert all rows in history order (newest first)."""
        # Remove all existing rows
        for row in self._rows.values():
            try:
                self._group.remove(row)
            except Exception:
                pass

        # Re-add in order (self._history is newest-first)
        for container, ts in self._history:
            if container in self._rows:
                self._group.add(self._rows[container])
