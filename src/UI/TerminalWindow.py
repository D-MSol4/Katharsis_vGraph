from gi.repository import Adw, GLib

from Data.Container import Container
from Messaging.Broker import Broker
from Messaging.Events import ContainerAttach
from UI.ApplicationWindow import ApplicationWindow
from UI.Terminal import Terminal


class TerminalWindow(ApplicationWindow):
    def __init__(self, terminal: Terminal, container: Container, *args, **kwargs):
        super().__init__(*args, **kwargs, title=container.name)
        self.terminal = terminal
        self.container = container
        self._closing = False
        terminal.grab_focus()
        self.lookup_action('copy').connect('activate', self.on_copy)
        self.lookup_action('paste').connect('activate', self.on_paste)
        self._toolbar = self._build_content()
        self.set_content(self._toolbar)
        self.terminal.connect("child_exited",
                              lambda t, s: self.close())
        self.connect("close-request",
                     lambda _: self.on_close_req())

    def on_copy(self, a, b):
        self.terminal.on_copy()

    def on_paste(self, a, b):
        self.terminal.on_paste()

    def on_close_req(self):
        if self._closing:
            return True
        self._closing = True
        
        # Remove terminal from ToolbarView properly
        self._toolbar.set_content(None)
        # Clear window content to disconnect widget tree
        self.set_content(None)
        
        Broker.notify(ContainerAttach(self.container, terminal=self.terminal))
        
        GLib.idle_add(self.destroy)
        return True  # Prevent default close handler

    def on_close(self, *args):
        self.on_close_req()

    def _build_content(self):
        ct = Adw.ToolbarView()
        ct.add_top_bar(Adw.HeaderBar(show_title=True))
        ct.set_content(self.terminal)
        return ct

