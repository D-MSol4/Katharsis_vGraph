from Kathara.manager.Kathara import Kathara
import docker as docker_lib
from gi.repository import Adw, Gtk, Gio

from Data.Container import Container
from Logic.TerminalManager import TerminalManager
from Messaging.Broker import Broker
from Messaging.Events import ReloadBegin, ContainersUpdate, ContainerDetach, \
    ContainerConnect, SetTerminal, LabSelect, WipeBegin, WipeFinish, LabStartFinish, LabStartBegin, OpenTerminal, \
    ContainerFocused, ContainerAttach
from UI.MainWindow import MainWindow
from UI.TerminalWindow import TerminalWindow


class Application(Adw.Application):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        self.connect("activate", self.on_activate)

        self.set_accels_for_action('win.copy', ['<Ctrl><Shift>c'])
        self.set_accels_for_action('win.paste', ['<Ctrl><Shift>v'])

        self.terminal_manager: TerminalManager = TerminalManager()
        self._docker_client = None

        self.dialog = Gtk.FileDialog()
        self.dialog.set_title("Select a lab directory")

        Broker.subscribe(ReloadBegin, self.on_reload_begin)
        Broker.subscribe(ContainerDetach, self.on_container_detach)
        Broker.subscribe(ContainerConnect, self.on_container_connect)

        Broker.subscribe(LabSelect, self.select_lab)
        Broker.subscribe(WipeBegin, self.on_wipe)
        Broker.subscribe(OpenTerminal, self.on_open_terminal)

        Broker.subscribe(ContainerFocused, self.on_container_focused)
        Broker.subscribe(ContainerAttach, self.on_container_attach)

    def _get_docker_client(self):
        if self._docker_client is None:
            self._docker_client = docker_lib.from_env()
        return self._docker_client

    def on_container_attach(self, event: ContainerAttach):
        if event.terminal is not None:
            # Emitted by TerminalWindow during close process; no need to close anything
            return
        term = self.terminal_manager.get_terminal(event.container)
        if term is not None:
            root = term.get_root()
            if hasattr(root, "close") and type(root).__name__ == "TerminalWindow":
                root.close()

    def on_container_focused(self, e: ContainerFocused):
        self.terminal_manager.get_terminal(e.container).get_root().present()

    def on_open_terminal(self, _):
        term = self.terminal_manager.shell()
        Broker.notify(SetTerminal(term, label="Shell"))

    def select_lab(self, _):
        self.dialog.select_folder(callback=self.on_lab_start)

    def on_lab_start(self, dialog: Gtk.FileDialog, response_id: Gio.AsyncResult):
        lab = dialog.select_folder_finish(response_id).get_path()
        Broker.notify(LabStartBegin())
        
        self.terminal_manager.set_cwd(lab)
        
        self.dialog.set_initial_folder(Gio.File.new_for_path(lab))

        term = self.terminal_manager.empty()
        term.connect("child_exited", lambda t, s: Broker.notify(ReloadBegin()) or Broker.notify(LabStartFinish()))
        term.run(["python", "-m", "kathara", "lrestart", "--noterminals", "-d", lab])
        Broker.notify(SetTerminal(term, label="Starting lab…"))

    def on_wipe(self, _):
        term = self.terminal_manager.empty()
        term.connect("child_exited", lambda t, s: Broker.notify(ReloadBegin()) or Broker.notify(WipeFinish()))
        term.run(["python", "-m", "kathara", "wipe"])
        Broker.notify(SetTerminal(term, label="Wiping lab…"))

    def on_activate(self, _):
        MainWindow(application=self).present()
        Broker.notify(ReloadBegin())

    def on_reload_begin(self, event):
        containers = Kathara.get_instance().get_machines_api_objects()
        result = []
        for c in containers:
            if c.status != 'running':
                continue
            # Extract collision domain names and interface details from the container's connected networks
            network_names = []
            interfaces = {}  # {clean_name: {"ip": ..., "mac": ..., "prefix_len": ...}}
            try:
                networks_dict = c.attrs.get("NetworkSettings", {}).get("Networks", {})
                for net_key, net_info in networks_dict.items():
                    # Skip Docker default bridge network
                    if net_key == "bridge" or net_key == "none":
                        continue
                    # Try to get the clean name from the Docker network labels
                    clean_name = net_key
                    try:
                        docker_net = self._get_docker_client().networks.get(net_key)
                        clean_name = docker_net.attrs.get("Labels", {}).get("name", net_key)
                    except Exception:
                        pass
                    network_names.append(clean_name)
                    # Extract interface details
                    interfaces[clean_name] = {
                        "ip": net_info.get("IPAddress", ""),
                        "mac": net_info.get("MacAddress", ""),
                        "prefix_len": str(net_info.get("IPPrefixLen", "")),
                    }
            except Exception:
                pass
            result.append(Container(c.labels['name'], c.labels['lab_hash'], network_names, interfaces))
        Broker.notify(ContainersUpdate(result))

    def on_container_detach(self, event: ContainerDetach):
        # Use the preserved terminal from the event if provided,
        # otherwise create a fresh one (e.g. sidebar detach)
        term = event.terminal if event.terminal is not None else self.terminal_manager.new_terminal(event.container)
        if term.get_parent() is not None:
            term.unparent()
        window = TerminalWindow(term, container=event.container)
        self.add_window(window)
        window.present()


    def on_container_connect(self, event: ContainerConnect):
        term = self.terminal_manager.new_terminal(event.container)
        Broker.notify(SetTerminal(term, label=event.container.name, container=event.container))
