from gi.repository import Vte, GLib, Gtk, Gio, Gdk, Adw


class Terminal(Vte.Terminal):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs,
                         allow_hyperlink=True,
                         bold_is_bright=True,
                         margin_bottom=10,
                         margin_end=10,
                         margin_start=10)
        self.set_clear_background(False)

        self._style_manager = Adw.StyleManager.get_default()
        self._style_manager.connect("notify::dark", self._on_theme_changed)
        self._apply_theme_colors()

        m = Gio.Menu()
        m.append("Copy", "win.copy")
        m.append("Paste", "win.paste")

        self.set_context_menu_model(m)

    def _on_theme_changed(self, *_):
        self._apply_theme_colors()

    def _apply_theme_colors(self):
        if self._style_manager.get_dark():
            # High-contrast palette for dark backgrounds.
            palette = [
                Gdk.RGBA(0.14, 0.16, 0.18, 1.0),
                Gdk.RGBA(0.90, 0.16, 0.16, 1.0),
                Gdk.RGBA(0.54, 0.89, 0.20, 1.0),
                Gdk.RGBA(0.99, 0.89, 0.33, 1.0),
                Gdk.RGBA(0.40, 0.60, 0.90, 1.0),
                Gdk.RGBA(0.80, 0.50, 0.80, 1.0),
                Gdk.RGBA(0.20, 0.80, 0.80, 1.0),
                Gdk.RGBA(0.80, 0.80, 0.80, 1.0),
                Gdk.RGBA(0.33, 0.34, 0.32, 1.0),
                Gdk.RGBA(1.00, 0.30, 0.30, 1.0),
                Gdk.RGBA(0.60, 1.00, 0.30, 1.0),
                Gdk.RGBA(1.00, 1.00, 0.40, 1.0),
                Gdk.RGBA(0.60, 0.80, 1.00, 1.0),
                Gdk.RGBA(0.90, 0.60, 0.90, 1.0),
                Gdk.RGBA(0.40, 1.00, 1.00, 1.0),
                Gdk.RGBA(1.00, 1.00, 1.00, 1.0),
            ]
            foreground = Gdk.RGBA(0.90, 0.90, 0.90, 1.0)
            background = Gdk.RGBA(0.11, 0.11, 0.13, 1.0)
        else:
            # Darker default text for readability on light mode.
            palette = [
                Gdk.RGBA(0.12, 0.12, 0.12, 1.0),
                Gdk.RGBA(0.72, 0.14, 0.14, 1.0),
                Gdk.RGBA(0.16, 0.52, 0.18, 1.0),
                Gdk.RGBA(0.60, 0.45, 0.10, 1.0),
                Gdk.RGBA(0.18, 0.34, 0.68, 1.0),
                Gdk.RGBA(0.52, 0.24, 0.52, 1.0),
                Gdk.RGBA(0.10, 0.50, 0.50, 1.0),
                Gdk.RGBA(0.72, 0.72, 0.72, 1.0),
                Gdk.RGBA(0.35, 0.35, 0.35, 1.0),
                Gdk.RGBA(0.82, 0.25, 0.25, 1.0),
                Gdk.RGBA(0.22, 0.62, 0.22, 1.0),
                Gdk.RGBA(0.72, 0.56, 0.14, 1.0),
                Gdk.RGBA(0.24, 0.44, 0.78, 1.0),
                Gdk.RGBA(0.62, 0.34, 0.62, 1.0),
                Gdk.RGBA(0.18, 0.60, 0.60, 1.0),
                Gdk.RGBA(0.98, 0.98, 0.98, 1.0),
            ]
            foreground = Gdk.RGBA(0.08, 0.08, 0.08, 1.0)
            background = Gdk.RGBA(0.95, 0.95, 0.95, 1.0)

        self.set_colors(foreground, background, palette)

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
