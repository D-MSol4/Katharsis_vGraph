from gi.repository import Vte, GLib, Gtk, Gio, Gdk


class Terminal(Vte.Terminal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs,
                         allow_hyperlink=True,
                         bold_is_bright=True,
                         margin_bottom=10,
                         margin_end=10,
                         margin_start=10)
        self.set_clear_background(False)
        
        # Define a readable palette for dark mode
        palette = [
            Gdk.RGBA(0.14, 0.16, 0.18, 1.0), # 0: Black
            Gdk.RGBA(0.90, 0.16, 0.16, 1.0), # 1: Red
            Gdk.RGBA(0.54, 0.89, 0.20, 1.0), # 2: Green
            Gdk.RGBA(0.99, 0.89, 0.33, 1.0), # 3: Yellow
            Gdk.RGBA(0.40, 0.60, 0.90, 1.0), # 4: Blue (bright enough for dark bg)
            Gdk.RGBA(0.80, 0.50, 0.80, 1.0), # 5: Magenta
            Gdk.RGBA(0.20, 0.80, 0.80, 1.0), # 6: Cyan
            Gdk.RGBA(0.80, 0.80, 0.80, 1.0), # 7: Light Gray
            Gdk.RGBA(0.33, 0.34, 0.32, 1.0), # 8: Dark Gray
            Gdk.RGBA(1.00, 0.30, 0.30, 1.0), # 9: Light Red
            Gdk.RGBA(0.60, 1.00, 0.30, 1.0), # 10: Light Green
            Gdk.RGBA(1.00, 1.00, 0.40, 1.0), # 11: Light Yellow
            Gdk.RGBA(0.60, 0.80, 1.00, 1.0), # 12: Light Blue
            Gdk.RGBA(0.90, 0.60, 0.90, 1.0), # 13: Light Magenta
            Gdk.RGBA(0.40, 1.00, 1.00, 1.0), # 14: Light Cyan
            Gdk.RGBA(1.00, 1.00, 1.00, 1.0), # 15: White
        ]
        self.set_colors(
            Gdk.RGBA(0.9, 0.9, 0.9, 1.0),     # Default foreground
            Gdk.RGBA(0.11, 0.11, 0.13, 1.0),  # Default background
            palette
        )

        m = Gio.Menu()
        m.append("Copy", "win.copy")
        m.append("Paste", "win.paste")

        self.set_context_menu_model(m)

    def on_copy(self):
        self.copy_clipboard_format(Vte.Format.TEXT)

    def on_paste(self):
        self.paste_clipboard()

    def run(self, command: list[str], cwd: str | None = None):
        # Pass the environment so utilities like bash get basic variables like PATH and LS_COLORS
        env = GLib.get_environ()
        self.spawn_async(
            Vte.PtyFlags.DEFAULT,
            cwd,
            command,
            env,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
        )
