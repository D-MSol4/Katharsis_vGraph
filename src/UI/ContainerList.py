from gi.repository import Gtk, Adw

from Data.Container import Container
from Messaging.Broker import Broker
from Messaging.Events import ContainersUpdate, \
    LabStartFinish, LabStartBegin, ContainerAdded
from UI.ContainerRow import ContainerRow


class ContainerList(Gtk.Box):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs,
                         orientation=Gtk.Orientation.VERTICAL,
                         vexpand=True)

        # Search entry at top
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search devices…",
            margin_start=10, margin_end=10, margin_top=6, margin_bottom=2,
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # Scrolled container for the list
        self.scroll = Gtk.ScrolledWindow(vexpand=True,
                                          margin_bottom=10,
                                          margin_start=10,
                                          margin_end=10)

        self.status_page = Adw.StatusPage(
            icon_name="dialog-information-symbolic",
            title="No running devices",
            description="Start a lab or reload to see your devices",
        )
        # Main vertical box that holds all collision domain groups
        self.groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.scroll.set_child(self.status_page)
        self.append(self.scroll)

        # Maps: collision_domain_name -> Adw.PreferencesGroup
        self.domain_groups: dict[str, Adw.PreferencesGroup] = {}
        # Maps: collision_domain_name -> number of rows in the group
        self.domain_row_counts: dict[str, int] = {}
        # Maps: Container -> list of (domain_name, ContainerRow) tuples
        self.container_rows: dict[Container, list[tuple[str, ContainerRow]]] = {}
        self.containers: set[Container] = set()

        Broker.subscribe(ContainersUpdate, self.on_containers_update)
        Broker.subscribe(LabStartBegin, self.disable_entries)
        Broker.subscribe(LabStartFinish, self.enable_entries)
        Broker.subscribe(ContainerAdded, self.on_container_attach)

    def disable_entries(self, _):
        for entries in self.container_rows.values():
            for _, row in entries:
                row.set_sensitive(False)

    def on_container_attach(self, event: ContainerAdded):
        if event.container in self.container_rows:
            for _, row in self.container_rows[event.container]:
                row.on_attach()

    def enable_entries(self, _):
        for entries in self.container_rows.values():
            for _, row in entries:
                row.set_sensitive(True)

    def _get_or_create_group(self, domain_name: str) -> Adw.PreferencesGroup:
        """Get or create an Adw.PreferencesGroup for a collision domain."""
        if domain_name not in self.domain_groups:
            group = Adw.PreferencesGroup(
                title=f" {domain_name} ",
            )
            self.domain_groups[domain_name] = group
            self.domain_row_counts[domain_name] = 0
            # Insert in alphabetical order: remove all, re-append sorted
            for existing_name in sorted(self.domain_groups.keys()):
                existing_group = self.domain_groups[existing_name]
                if existing_group.get_parent() is self.groups_box:
                    self.groups_box.remove(existing_group)
            for sorted_name in sorted(self.domain_groups.keys()):
                self.groups_box.append(self.domain_groups[sorted_name])
        return self.domain_groups[domain_name]

    def _remove_group_if_empty(self, domain_name: str):
        """Remove a group if it no longer contains any rows."""
        if self.domain_row_counts.get(domain_name, 0) <= 0:
            group = self.domain_groups.pop(domain_name, None)
            self.domain_row_counts.pop(domain_name, None)
            if group is not None:
                self.groups_box.remove(group)

    def build_row(self, container: Container):
        row = ContainerRow(container)
        return row

    def add_container(self, container: Container):
        entries = []
        if container.networks:
            for net_name in sorted(container.networks):
                # Show group if domain has >1 device, or if it's the device's ONLY domain
                if self.domain_counts.get(net_name, 0) > 1 or len(container.networks) == 1:
                    group = self._get_or_create_group(net_name)
                    row = self.build_row(container)
                    group.add(row)
                    self.domain_row_counts[net_name] += 1
                    entries.append((net_name, row))
        else:
            # Container without any collision domain
            domain = "Not connected"
            group = self._get_or_create_group(domain)
            row = self.build_row(container)
            group.add(row)
            self.domain_row_counts[domain] += 1
            entries.append((domain, row))
        self.container_rows[container] = entries

    def on_containers_update(self, event: ContainersUpdate):
        self.containers = set(event.containers)
        
        # Clear existing ui
        while child := self.groups_box.get_first_child():
            self.groups_box.remove(child)
        self.domain_groups.clear()
        self.domain_row_counts.clear()
        self.container_rows.clear()
        
        if not self.containers:
            self.scroll.set_child(self.status_page)
            return

        self.scroll.set_child(self.groups_box)

        # Pre-calculate domain counts
        import collections
        self.domain_counts = collections.Counter()
        for c in self.containers:
            self.domain_counts.update(c.networks if c.networks else ["Not connected"])
                
        # Fill completely
        for container in sorted(self.containers, key=lambda c: c.name):
            self.add_container(container)

        # Re-apply any active search filter
        self._apply_filter(self.search_entry.get_text())

    def remove_container(self, container: Container):
        entries = self.container_rows.pop(container, [])
        for domain_name, row in entries:
            if domain_name in self.domain_groups:
                self.domain_groups[domain_name].remove(row)
                self.domain_row_counts[domain_name] -= 1
                self._remove_group_if_empty(domain_name)

    # ── Search / Filter ────────────────────────────────────────────────

    def _on_search_changed(self, entry):
        self._apply_filter(entry.get_text())

    def _apply_filter(self, query: str):
        q = query.strip().lower()
        # Track which groups have visible rows
        group_visible = {name: False for name in self.domain_groups}

        for container, entries in self.container_rows.items():
            matches = not q or q in container.name.lower()
            for domain_name, row in entries:
                row.set_visible(matches)
                if matches:
                    group_visible[domain_name] = True

        # Hide/show entire groups
        for name, group in self.domain_groups.items():
            group.set_visible(group_visible.get(name, False) or not q)

