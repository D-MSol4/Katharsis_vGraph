from gi.repository import Adw, Gtk

from Messaging.Broker import Broker
from Messaging.Events import ContainerDisconnected, LabStartBegin, ContainerAdded, ContainerAttach
from Messaging.Events import LabSelect, WipeBegin, ReloadBegin, ContainerConnect, SetTerminal, ContainerDetach, \
    WipeFinish, LabStartFinish, ContainerDeleted, OpenTerminal
from Messaging.Events import Shutdown
from UI.ApplicationWindow import ApplicationWindow
from UI.ContainerList import ContainerList
from UI.InitialTerminal import InitialTerminal
from UI.NetworkGraphView import NetworkGraphView
from UI.Terminal import Terminal


class MainWindow(ApplicationWindow):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, title="Katharsis")

        # Build content panel with a stack (terminal + graph)
        self.content_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
            transition_duration=200,
        )

        # Terminal placeholder
        self.terminal_box = Gtk.Box()
        self.content_stack.add_named(self.terminal_box, "terminal")

        # Graph view
        self.graph_view = NetworkGraphView()
        self.content_stack.add_named(self.graph_view, "graph")

        self.content_stack.set_visible_child_name("terminal")

        self.set_content(Adw.OverlaySplitView(sidebar=self.get_sidebar(), content=self.get_panel()))

        self.lookup_action('copy').connect('activate', self.on_copy)
        self.lookup_action('paste').connect('activate', self.on_paste)

        self.connect("close-request", lambda _: Broker.notify(Shutdown()))

        self.terminal = None
        self.current_container = None  # Container shown in the terminal
        self.switch_terminal(InitialTerminal())

        Broker.subscribe(SetTerminal, self.set_terminal)
        Broker.subscribe(ContainerAdded, self.on_added)
        Broker.subscribe(ContainerConnect, self._on_container_connect)
        Broker.subscribe(ContainerDisconnected, self._on_container_gone)
        Broker.subscribe(ContainerDeleted, self._on_container_gone)
        Broker.subscribe(WipeFinish, lambda _: self._clear_current_container())
        Broker.subscribe(LabStartFinish, lambda _: self._clear_current_container())
        Broker.subscribe(ContainerAttach, self._on_container_reattach)

    def on_added(self, e: ContainerAdded):
        panel = self.get_content().get_content()  # Adw.ToolbarView
        current_child = panel.get_content()  # Gtk.Stack
        visible = current_child.get_visible_child_name() if hasattr(current_child, 'get_visible_child_name') else None
        if visible == "terminal":
            # Check if the terminal box has content
            term_content = self.terminal_box.get_first_child()
            if (term_content is None) or (term_content is not self.terminal):
                Broker.notify(ContainerConnect(e.container))

    def on_copy(self, a, b):
        self.terminal.on_copy()

    def on_paste(self, a, b):
        self.terminal.on_paste()

    def get_sidebar(self):
        sb = Adw.ToolbarView()
        tb = Adw.HeaderBar(show_title=True)
        bt = Gtk.Button(icon_name="utilities-terminal-symbolic",
                        tooltip_text="Open a bash terminal with access to kathara")
        bt.connect("clicked", lambda _: Broker.notify(OpenTerminal()))
        tb.pack_start(bt)
        sb.add_top_bar(tb)
        sb.add_bottom_bar(self.get_main_buttons())
        sb.set_content(ContainerList())
        return sb

    def get_main_buttons(self):
        mb = Gtk.Box(margin_end=10,
                     margin_start=10,
                     margin_bottom=10,
                     spacing=10)
        start = Gtk.Button(
            icon_name="media-playback-start-symbolic",
            tooltip_text="Start or restart a lab",
            hexpand=True
        )
        start.connect("clicked", lambda _: Broker.notify(LabSelect()))
        mb.append(start)

        wipe = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Wipe all labs",
            hexpand=True
        )
        wipe.connect("clicked", lambda _: Broker.notify(WipeBegin()))
        mb.append(wipe)

        reload = Gtk.Button(
            icon_name="view-refresh-symbolic",
            tooltip_text="Reload running containers",
            hexpand=True
        )
        reload.connect("clicked", lambda _: Broker.notify(ReloadBegin()))
        mb.append(reload)

        return mb

    def get_topbar(self):
        wt = Adw.WindowTitle(title="")

        Broker.subscribe(ContainerConnect, lambda e: wt.set_title(e.container.name))
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

        # Detach button — open current terminal in a separate window
        self.detach_btn = Gtk.Button(
            icon_name="window-new-symbolic",
            tooltip_text="Open terminal in a separate window",
            visible=False,
        )
        self.detach_btn.connect("clicked", self._on_detach_clicked)
        hb.pack_start(self.detach_btn)

        # Toggle button for graph view
        self.graph_toggle = Gtk.ToggleButton(
            icon_name="network-workgroup-symbolic",
            tooltip_text="Show/hide the network topology graph",
        )
        self.graph_toggle.connect("toggled", self._on_graph_toggled)
        hb.pack_end(self.graph_toggle)

        return hb

    def _on_container_connect(self, event: ContainerConnect):
        self.current_container = event.container
        self.detach_btn.set_visible(True)
        self.detach_btn.set_icon_name("window-new-symbolic")
        self.detach_btn.set_tooltip_text(f"Open {event.container.name} in a separate window")

    def _on_container_gone(self, event):
        if self.current_container is not None and self.current_container == event.container:
            self._clear_current_container()

    def _on_container_reattach(self, event: ContainerAttach):
        if self.current_container is not None and self.current_container == event.container:
            self.detach_btn.set_icon_name("window-new-symbolic")
            self.detach_btn.set_tooltip_text(f"Open {event.container.name} in a separate window")

    def _clear_current_container(self):
        self.current_container = None
        self.detach_btn.set_visible(False)

    def _on_detach_clicked(self, _):
        if self.current_container is not None:
            container = self.current_container
            self._clear_current_container()
            self.terminal = None
            Broker.notify(ContainerDetach(container))

    def _on_graph_toggled(self, button):
        if button.get_active():
            self.content_stack.set_visible_child_name("graph")
            # Recalculate the graph layout when shown
            self.graph_view._recalculate()
        else:
            self.content_stack.set_visible_child_name("terminal")

    def get_panel(self):
        ct = Adw.ToolbarView()
        ct.add_top_bar(self.get_topbar())
        ct.set_content(self.content_stack)
        return ct

    def set_terminal(self, event: SetTerminal):
        self.switch_terminal(event.terminal)
        # Switch back to terminal view when a terminal is set
        if self.graph_toggle.get_active():
            self.graph_toggle.set_active(False)

    def switch_terminal(self, terminal: Terminal):
        # Check if terminal is already displayed
        if terminal.get_parent() is self.terminal_box:
            return
        self.terminal = terminal
        # Remove old content from terminal_box
        old = self.terminal_box.get_first_child()
        if old is not None:
            self.terminal_box.remove(old)
        self.terminal_box.append(self.terminal)
        self.terminal.set_hexpand(True)
        self.terminal.set_vexpand(True)
        self.terminal.grab_focus()
