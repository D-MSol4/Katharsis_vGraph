from Data.Container import Container
from Messaging.Broker import Broker
from Messaging.Events import ContainerDisconnected
from UI.Terminal import Terminal


class TerminalManager:
    connect_script = """
from Kathara.manager.Kathara import Kathara
print("Connecting to", '{container.name}' + '...')
Kathara.get_instance().connect_tty(machine_name='{container.name}', lab_hash='{container.lab_hash}')
"""
    __empty = Terminal()

    def __init__(self):
        self.container_terminals: dict[Container, Terminal] = {}
        self.cwd: str | None = None

    def set_cwd(self, cwd: str):
        self.cwd = cwd

    def empty(self):
        self.__empty.reset(True, True)
        return self.__empty

    def shell(self):
        import os
        import sys
        
        # Create a virtual bin directory for the 'kathara' command wrapper
        bin_dir = "/tmp/katharsis_bin"
        os.makedirs(bin_dir, exist_ok=True)
        kathara_path = os.path.join(bin_dir, "kathara")
        try:
            with open(kathara_path, "w") as f:
                f.write(f"#!/bin/sh\nexec \"{sys.executable}\" -m kathara \"$@\"\n")
            os.chmod(kathara_path, 0o755)
        except OSError:
            pass  # Ignore if we don't have permissions
            
        current_path = os.environ.get("PATH", "")
        if bin_dir not in current_path.split(os.pathsep):
            os.environ["PATH"] = f"{bin_dir}{os.pathsep}{current_path}"

        shell_term = Terminal()
        shell_cmd = os.environ.get("SHELL", "/bin/bash")
        shell_term.run([shell_cmd], cwd=self.cwd)
        return shell_term

    def get_terminal(self, container: Container):
        if container in self.container_terminals:
            return self.container_terminals[container]

        terminal = Terminal()
        terminal.run(
            [
                "python",
                "-c",
                self.connect_script.format(container=container)
            ])
        terminal.connect("child_exited", self.on_terminal_exited, container)

        self.container_terminals[container] = terminal
        return terminal

    def new_terminal(self, container: Container) -> Terminal:
        """Always create a new independent terminal session for a container."""
        terminal = Terminal()
        terminal.run([
            "python",
            "-c",
            self.connect_script.format(container=container)
        ])
        terminal.connect("child_exited", self.on_terminal_exited, container)
        # Don't store in container_terminals dict — each is independent
        return terminal

    def on_terminal_exited(self, term: Terminal, status: int, container: Container):
        self.container_terminals.pop(container, None)
        Broker.notify(ContainerDisconnected(container))
