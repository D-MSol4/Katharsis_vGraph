from gi.repository import Adw, Gtk, Gdk

from Messaging.Broker import Broker
from Messaging.Events import ContainerDisconnected, LabStartBegin, ContainerAdded, ContainerAttach
from Messaging.Events import LabSelect, WipeBegin, ReloadBegin, ContainerConnect, SetTerminal, ContainerDetach, \
    WipeFinish, LabStartFinish, ContainerDeleted, OpenTerminal
from Messaging.Events import Shutdown
from UI.ApplicationWindow import ApplicationWindow
from UI.ConnectionHistory import ConnectionHistory
from UI.ContainerList import ContainerList
from UI.NetworkGraphView import NetworkGraphView
from UI.Terminal import Terminal

PANE_CSS = ".active-pane > menubutton > button { background: alpha(@accent_color, 0.18); }"


class TerminalPane(Gtk.Box):
    """Single pane: TabBar + TabView."""

    def __init__(self, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self.tab_view = Adw.TabView()
        self.tab_bar = Adw.TabBar(view=self.tab_view, autohide=False)
        self.tab_view.set_vexpand(True)
        self.tab_view.set_hexpand(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.append(self.tab_bar)
        self.append(self.tab_view)

    def add_terminal(self, terminal: Terminal, label: str) -> Adw.TabPage:
        # Wrap terminal in a Box so we can safely extract it later.
        # Extracting from Gtk.Box.remove() is reliable, unlike from
        # AdwTabView internals which corrupt state or destroy the widget.
        wrapper = Gtk.Box(hexpand=True, vexpand=True)
        terminal.set_hexpand(True)
        terminal.set_vexpand(True)
        wrapper.append(terminal)
        page = self.tab_view.append(wrapper)
        page.set_title(label)
        self.tab_view.set_selected_page(page)
        terminal.grab_focus()
        return page


class MainWindow(ApplicationWindow):
    MAX_PANES = 6

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, title="Katharsis")

        # CSS for active pane highlight
        css = Gtk.CssProvider()
        css.load_from_string(PANE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Create pool of panes
        self._panes: list[TerminalPane] = []
        for i in range(self.MAX_PANES):
            pane = TerminalPane()
            pane.tab_view.connect("close-page", self._on_tab_close_request)
            # Tab switch within a pane → update title/detach
            pane.tab_view.connect("notify::selected-page",
                                  lambda tv, _param, idx=i: self._update_current_state(idx))
            # Focus enters pane (clicking terminal, tab bar) → make this the active pane
            focus_ctrl = Gtk.EventControllerFocus()
            focus_ctrl.connect("enter", lambda ctrl, idx=i: self._update_current_state(idx))
            pane.add_controller(focus_ctrl)
            self._panes.append(pane)

        self._pane_count = 0
        self._active_pane_idx = 0
        self._page_pane: dict[Adw.TabPage, TerminalPane] = {}
        self._page_container: dict[Adw.TabPage, object] = {}
        self.current_container = None

        # Terminal container holds the paned layout
        self._terminal_container = Gtk.Box(hexpand=True, vexpand=True)

        # Build initial 1-pane layout
        self._rebuild_layout(1)

        # Stack: terminal area + graph
        self.content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=200)
        self.content_stack.add_named(self._terminal_container, "terminal")
        self.graph_view = NetworkGraphView()
        self.content_stack.add_named(self.graph_view, "graph")
        self.content_stack.set_visible_child_name("terminal")

        self.set_content(Adw.OverlaySplitView(sidebar=self.get_sidebar(), content=self.get_panel()))
        self.lookup_action('copy').connect('activate', self.on_copy)
        self.lookup_action('paste').connect('activate', self.on_paste)
        self.connect("close-request", lambda _: Broker.notify(Shutdown()))

        Broker.subscribe(SetTerminal, self.set_terminal)
        Broker.subscribe(ContainerAdded, self.on_added)
        Broker.subscribe(ContainerDisconnected, self._on_container_disconnected)
        Broker.subscribe(ContainerDeleted, self._on_container_disconnected)
        Broker.subscribe(WipeFinish, lambda _: self._on_wipe_or_restart())
        Broker.subscribe(LabStartFinish, lambda _: self._on_wipe_or_restart())
        Broker.subscribe(ContainerAttach, self._on_container_attach)

    # ── Pane layout ─────────────────────────────────────────────────────

    def _active_pane(self) -> TerminalPane:
        return self._panes[min(self._active_pane_idx, self._pane_count - 1)]

    def _set_active_pane(self, idx: int):
        if idx >= self._pane_count:
            idx = 0
        self._active_pane_idx = idx
        for i, pane in enumerate(self._panes[:self._pane_count]):
            if i == idx:
                pane.tab_bar.add_css_class("active-pane")
            else:
                pane.tab_bar.remove_css_class("active-pane")

    def _update_current_state(self, pane_idx: int):
        """Called when the selected tab changes in any pane. Updates active pane,
        current_container, title, and detach button."""
        self._set_active_pane(pane_idx)
        pane = self._panes[pane_idx]
        page = pane.tab_view.get_selected_page()
        if page is None:
            self._clear_current_container()
            self._window_title.set_title("")
            return
        container = self._page_container.get(page)
        if container is not None:
            self.current_container = container
            self.detach_btn.set_visible(True)
            self.detach_btn.set_tooltip_text(f"Open {container.name} in a separate window")
            self._window_title.set_title(container.name)
        else:
            self._clear_current_container()
            title = page.get_title()
            self._window_title.set_title(title if title else "")

    def _rebuild_layout(self, n: int):
        n = max(1, min(n, self.MAX_PANES))

        # Move tabs from excess panes → pane 0
        if n < self._pane_count:
            for i in range(n, self._pane_count):
                self._move_all_tabs(self._panes[i], self._panes[0])

        # Unparent all panes cleanly
        for pane in self._panes:
            p = pane.get_parent()
            if p is None:
                continue
            if isinstance(p, Gtk.Paned):
                if p.get_start_child() is pane:
                    p.set_start_child(None)
                elif p.get_end_child() is pane:
                    p.set_end_child(None)
            elif hasattr(p, 'remove'):
                p.remove(pane)

        # Clear container
        child = self._terminal_container.get_first_child()
        while child is not None:
            self._terminal_container.remove(child)
            child = self._terminal_container.get_first_child()

        # Build new grid
        root = self._build_grid(self._panes[:n])
        self._terminal_container.append(root)
        self._pane_count = n

        if self._active_pane_idx >= n:
            self._set_active_pane(0)

    def _move_all_tabs(self, src: TerminalPane, dst: TerminalPane):
        while src.tab_view.get_n_pages() > 0:
            page = src.tab_view.get_nth_page(0)
            pos = dst.tab_view.get_n_pages()
            src.tab_view.transfer_page(page, dst.tab_view, pos)
            if page in self._page_pane:
                self._page_pane[page] = dst

    def _build_grid(self, panes: list):
        n = len(panes)
        if n == 1:
            return panes[0]
        if n <= 3:
            return self._h_chain(panes)
        mid = (n + 1) // 2
        top = self._h_chain(panes[:mid])
        bot = self._h_chain(panes[mid:])
        return self._v_paned(top, bot)

    def _h_chain(self, panes: list):
        if len(panes) == 1:
            return panes[0]
        p = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                      hexpand=True, vexpand=True,
                      shrink_start_child=False, shrink_end_child=False,
                      resize_start_child=True, resize_end_child=True)
        p.set_start_child(panes[0])
        p.set_end_child(self._h_chain(panes[1:]))
        return p

    def _v_paned(self, top, bot):
        p = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL,
                      hexpand=True, vexpand=True,
                      shrink_start_child=False, shrink_end_child=False,
                      resize_start_child=True, resize_end_child=True)
        p.set_start_child(top)
        p.set_end_child(bot)
        return p

    # ── Tab close ───────────────────────────────────────────────────────

    def _on_tab_close_request(self, tab_view: Adw.TabView, page: Adw.TabPage) -> bool:
        self._page_pane.pop(page, None)
        container = self._page_container.pop(page, None)
        tab_view.close_page_finish(page, True)
        if container is not None and container is self.current_container:
            self._clear_current_container()
        return True

    # ── Broker events ───────────────────────────────────────────────────

    def on_added(self, e: ContainerAdded):
        if self.content_stack.get_visible_child_name() == "terminal":
            Broker.notify(ContainerConnect(e.container))

    def set_terminal(self, event: SetTerminal):
        pane = self._active_pane()
        if self.graph_toggle.get_active():
            self.graph_toggle.set_active(False)
        page = pane.add_terminal(event.terminal, event.label)
        self._page_pane[page] = pane
        if event.container is not None:
            self._page_container[page] = event.container
            self.current_container = event.container
            self.detach_btn.set_visible(True)
            self.detach_btn.set_tooltip_text(f"Open {event.container.name} in a separate window")
        else:
            self._clear_current_container()

    def _on_container_disconnected(self, event):
        container = event.container
        for page, c in list(self._page_container.items()):
            if c is container:
                pane = self._page_pane.get(page)
                if pane is not None:
                    pane.tab_view.close_page(page)
        if self.current_container is container:
            self._clear_current_container()

    def _on_wipe_or_restart(self):
        for pane in self._panes[:self._pane_count]:
            while pane.tab_view.get_n_pages() > 0:
                page = pane.tab_view.get_nth_page(0)
                self._page_pane.pop(page, None)
                self._page_container.pop(page, None)
                pane.tab_view.close_page(page)
        self._clear_current_container()

    # ── Detach ──────────────────────────────────────────────────────────

    def _on_detach_clicked(self, _):
        # Detach the currently selected terminal in the active pane
        pane = self._active_pane()
        page = pane.tab_view.get_selected_page()
        if page is None:
            return
        container = self._page_container.get(page)
        if container is None:
            return

        # Extract terminal from its Box wrapper (safe, reliable)
        wrapper = page.get_child()
        terminal = wrapper.get_first_child() if wrapper else None
        if terminal is None:
            return
        wrapper.remove(terminal)

        # Clean up tracking
        self._page_pane.pop(page, None)
        self._page_container.pop(page, None)
        self._clear_current_container()

        # Close the page (destroys the empty wrapper Box — safe)
        pane.tab_view.close_page(page)

        # Pass the preserved terminal to Application
        Broker.notify(ContainerDetach(container, terminal=terminal))

    def _on_container_attach(self, event: ContainerAttach):
        """Terminal window closed — re-add the preserved terminal as a tab."""
        print(f"DEBUG: MainWindow._on_container_attach received ContainerAttach for {event.container} with terminal {event.terminal}")
        terminal = event.terminal
        if terminal is not None:
            if terminal.get_parent() is not None:
                terminal.unparent()
            # Ensure terminal view is visible
            self.content_stack.set_visible_child_name("terminal")
            pane = self._active_pane()
            page = pane.add_terminal(terminal, event.container.name)
            self._page_pane[page] = pane
            self._page_container[page] = event.container
            self.current_container = event.container
            self.detach_btn.set_visible(True)
            self._window_title.set_title(event.container.name)
        else:
            Broker.notify(ContainerConnect(event.container))

    def _clear_current_container(self):
        self.current_container = None
        self.detach_btn.set_visible(False)

    # ── Copy / Paste ────────────────────────────────────────────────────

    def on_copy(self, a, b):
        page = self._active_pane().tab_view.get_selected_page()
        if page:
            wrapper = page.get_child()
            if wrapper:
                term = wrapper.get_first_child()
                if term:
                    term.on_copy()

    def on_paste(self, a, b):
        page = self._active_pane().tab_view.get_selected_page()
        if page:
            wrapper = page.get_child()
            if wrapper:
                term = wrapper.get_first_child()
                if term:
                    term.on_paste()

    # ── Sidebar ─────────────────────────────────────────────────────────

    def get_sidebar(self):
        sb = Adw.ToolbarView()
        tb = Adw.HeaderBar(show_title=True)
        bt = Gtk.Button(icon_name="utilities-terminal-symbolic",
                        tooltip_text="Open a bash terminal with access to kathara")
        bt.connect("clicked", lambda _: Broker.notify(OpenTerminal()))
        tb.pack_start(bt)
        sb.add_top_bar(tb)
        sb.add_bottom_bar(self.get_main_buttons())

        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_content.append(ContainerList())
        sidebar_content.append(ConnectionHistory())

        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(sidebar_content)
        sb.set_content(scroll)
        return sb

    def get_main_buttons(self):
        mb = Gtk.Box(margin_end=10, margin_start=10, margin_top=10,
                     margin_bottom=10, spacing=10)
        for icon, tip, event in [
            ("media-playback-start-symbolic", "Start or restart a lab", LabSelect()),
            ("user-trash-symbolic", "Wipe all labs", WipeBegin()),
            ("view-refresh-symbolic", "Reload running containers", ReloadBegin()),
        ]:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, hexpand=True)
            e = event
            btn.connect("clicked", lambda _, ev=e: Broker.notify(ev))
            mb.append(btn)
        return mb

    # ── Top bar ─────────────────────────────────────────────────────────

    def get_topbar(self):
        wt = Adw.WindowTitle(title="")
        self._window_title = wt  # Save reference for _on_page_selected
        self.current_container = None

        # Title is now mainly driven by _on_page_selected, but these events
        # still handle special states (wipe, lab start, etc.)
        Broker.subscribe(WipeBegin, lambda _: wt.set_title("Wiping lab..."))
        Broker.subscribe(LabStartBegin, lambda _: wt.set_title("(Re)starting lab..."))
        Broker.subscribe(ContainerDetach,
                         lambda e: wt.set_title("" if wt.get_title() == e.container.name else wt.get_title()))
        Broker.subscribe(ContainerDeleted,
                         lambda e: wt.set_title("" if wt.get_title() == e.container.name else wt.get_title()))
        Broker.subscribe(WipeFinish, lambda _: wt.set_title(""))
        Broker.subscribe(LabStartFinish, lambda _: wt.set_title(""))
        Broker.subscribe(ContainerDisconnected,
                         lambda e: wt.set_title("" if wt.get_title() == e.container.name else wt.get_title()))
        Broker.subscribe(OpenTerminal, lambda _: wt.set_title("Integrated shell, good luck"))

        hb = Adw.HeaderBar(show_title=True, title_widget=wt)

        # Detach button
        self.detach_btn = Gtk.Button(
            icon_name="window-new-symbolic",
            tooltip_text="Open terminal in a separate window",
            visible=False)
        self.detach_btn.connect("clicked", self._on_detach_clicked)
        hb.pack_start(self.detach_btn)

        # Pane count spinner
        adj = Gtk.Adjustment(value=1, lower=1, upper=self.MAX_PANES, step_increment=1)
        self.pane_spin = Gtk.SpinButton(
            adjustment=adj, digits=0, numeric=True,
            tooltip_text="Number of terminal panes",
            width_chars=2)
        self.pane_spin.connect("value-changed",
                               lambda s: self._rebuild_layout(int(s.get_value())))
        hb.pack_end(self.pane_spin)

        # Graph toggle
        self.graph_toggle = Gtk.ToggleButton(
            icon_name="network-workgroup-symbolic",
            tooltip_text="Show/hide the network topology graph")
        self.graph_toggle.connect("toggled", self._on_graph_toggled)
        hb.pack_end(self.graph_toggle)

        return hb

    def _on_graph_toggled(self, button):
        if button.get_active():
            self.content_stack.set_visible_child_name("graph")
            self.graph_view._recalculate()
        else:
            self.content_stack.set_visible_child_name("terminal")

    def get_panel(self):
        ct = Adw.ToolbarView()
        ct.add_top_bar(self.get_topbar())
        ct.set_content(self.content_stack)
        return ct
