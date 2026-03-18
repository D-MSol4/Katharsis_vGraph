import math
import random
import cairo

from gi.repository import Gtk, Gdk

from Data.Container import Container
from Messaging.Broker import Broker
from Messaging.Events import ContainersUpdate, ContainerConnect


# ---------------------------------------------------------------------------
# Force-directed layout (Fruchterman-Reingold style)
# ---------------------------------------------------------------------------

def _force_directed_layout(containers, width, height, iterations=120):
    """Compute positions using a force-directed algorithm.

    Nodes = devices (circles) + collision domains (small squares).
    Edges  = device ↔ domain for every network the device belongs to.

    Returns:
        device_nodes: list of (container, x, y)
        domain_nodes: dict domain_name -> (x, y)
        edges: list of (dev_x, dev_y, dom_x, dom_y, domain_name)
    """
    # --- Build the node list ---
    # Collect domains
    all_domains: set[str] = set()
    for c in containers:
        nets = c.networks if c.networks else ["Not connected"]
        all_domains.update(nets)

    all_domains_list = sorted(all_domains)
    n_devices = len(containers)
    n_domains = len(all_domains_list)
    total = n_devices + n_domains

    if total == 0:
        return [], {}, []

    # Assign indices: 0..n_devices-1 = devices, n_devices..total-1 = domains
    sorted_containers = sorted(containers, key=lambda c: c.name)
    domain_index = {d: n_devices + i for i, d in enumerate(all_domains_list)}

    # Build adjacency (edge list)
    edge_pairs = []  # (i, j) pairs
    for ci, c in enumerate(sorted_containers):
        nets = c.networks if c.networks else ["Not connected"]
        for net in nets:
            edge_pairs.append((ci, domain_index[net]))

    # --- Initialise positions ---
    margin = 80
    usable_w = max(width - 2 * margin, 200)
    usable_h = max(height - 2 * margin, 200)
    cx, cy = width / 2, height / 2

    # Seed positions: place on a circle with some randomness
    pos_x = [0.0] * total
    pos_y = [0.0] * total
    rng = random.Random(42)  # deterministic seed for reproducibility
    for i in range(total):
        angle = 2 * math.pi * i / total
        r = min(usable_w, usable_h) * 0.35
        pos_x[i] = cx + r * math.cos(angle) + rng.uniform(-20, 20)
        pos_y[i] = cy + r * math.sin(angle) + rng.uniform(-20, 20)

    # --- Fruchterman-Reingold ---
    area_val = usable_w * usable_h
    k = math.sqrt(area_val / max(total, 1)) * 1.2  # ideal edge length
    k2 = k * k

    temp = min(usable_w, usable_h) * 0.15  # initial temperature
    cool = temp / (iterations + 1)

    for iteration in range(iterations):
        # Displacement vectors
        disp_x = [0.0] * total
        disp_y = [0.0] * total

        # Repulsive forces between all pairs
        for i in range(total):
            for j in range(i + 1, total):
                dx = pos_x[i] - pos_x[j]
                dy = pos_y[i] - pos_y[j]
                dist2 = dx * dx + dy * dy
                if dist2 < 0.01:
                    dist2 = 0.01
                    dx = rng.uniform(-0.1, 0.1)
                    dy = rng.uniform(-0.1, 0.1)
                force = k2 / dist2  # repulsive magnitude (actually k²/d)
                dist = math.sqrt(dist2)
                fx = dx / dist * force
                fy = dy / dist * force
                disp_x[i] += fx
                disp_y[i] += fy
                disp_x[j] -= fx
                disp_y[j] -= fy

        # Attractive forces along edges
        for (i, j) in edge_pairs:
            dx = pos_x[i] - pos_x[j]
            dy = pos_y[i] - pos_y[j]
            dist = math.sqrt(dx * dx + dy * dy)
            if dist < 0.01:
                dist = 0.01
            force = dist * dist / k  # attractive magnitude (d²/k)
            fx = dx / dist * force
            fy = dy / dist * force
            disp_x[i] -= fx
            disp_y[i] -= fy
            disp_x[j] += fx
            disp_y[j] += fy

        # Apply displacements (clamped by temperature)
        for i in range(total):
            disp_len = math.sqrt(disp_x[i] ** 2 + disp_y[i] ** 2)
            if disp_len > 0:
                scale = min(disp_len, temp) / disp_len
                pos_x[i] += disp_x[i] * scale
                pos_y[i] += disp_y[i] * scale

            # Keep within bounds
            pos_x[i] = max(margin, min(width - margin, pos_x[i]))
            pos_y[i] = max(margin, min(height - margin, pos_y[i]))

        temp -= cool

    # --- Build results ---
    device_nodes = []
    for ci, c in enumerate(sorted_containers):
        device_nodes.append((c, pos_x[ci], pos_y[ci]))

    domain_nodes = {}
    for d_name, d_idx in domain_index.items():
        domain_nodes[d_name] = (pos_x[d_idx], pos_y[d_idx])

    edges = []
    for c, dx, dy in device_nodes:
        nets = c.networks if c.networks else ["Not connected"]
        for net in nets:
            dom_x, dom_y = domain_nodes[net]
            edges.append((dx, dy, dom_x, dom_y, net))

    return device_nodes, domain_nodes, edges


# --- Constants ---
DEVICE_RADIUS = 24
DOMAIN_RADIUS = 7
HIT_RADIUS = DEVICE_RADIUS + 8
MIN_CANVAS = 550

# Color palette
COL_BG = (0.11, 0.11, 0.13)
COL_EDGE = (0.55, 0.72, 1.0, 0.35)
COL_DEVICE = (0.22, 0.47, 0.88, 0.9)
COL_DEVICE_HOVER = (0.35, 0.6, 1.0, 0.95)
COL_DEVICE_BORDER = (0.5, 0.75, 1.0, 1.0)
COL_ENDDEVICE = (0.85, 0.55, 0.2, 0.9)
COL_ENDDEVICE_HOVER = (0.95, 0.65, 0.3, 0.95)
COL_ENDDEVICE_BORDER = (1.0, 0.75, 0.4, 1.0)
COL_GLOW = (0.4, 0.65, 1.0, 0.2)
COL_GLOW_END = (0.9, 0.6, 0.2, 0.2)
COL_DOMAIN_FILL = (0.28, 0.70, 0.42, 0.85)
COL_DOMAIN_BORDER = (0.4, 0.88, 0.55, 1.0)
COL_DOMAIN_TEXT = (0.85, 1.0, 0.88)
COL_TEXT = (1.0, 1.0, 1.0)


class NetworkGraphView(Gtk.ScrolledWindow):
    """A scrollable network topology graph view drawn with Cairo (GNS3-style)."""

    ZOOM_MIN = 0.3
    ZOOM_MAX = 3.0
    ZOOM_STEP = 0.1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, vexpand=True, hexpand=True)

        self.containers: list[Container] = []
        self.device_nodes = []
        self.domain_nodes = {}
        self.edges = []

        self.hover_container = None
        self.zoom_level = 1.0
        self._base_w = MIN_CANVAS
        self._base_h = MIN_CANVAS

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_draw_func(self._draw)
        self.drawing_area.set_content_width(MIN_CANVAS)
        self.drawing_area.set_content_height(MIN_CANVAS)

        # Click gesture
        click = Gtk.GestureClick()
        click.connect("released", self._on_click)
        self.drawing_area.add_controller(click)

        # Motion for hover
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.drawing_area.add_controller(motion)

        # Scroll zoom
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll.connect("scroll", self._on_scroll)
        self.drawing_area.add_controller(scroll)

        # Pinch zoom
        zoom_gesture = Gtk.GestureZoom()
        zoom_gesture.connect("scale-changed", self._on_zoom_gesture)
        self.drawing_area.add_controller(zoom_gesture)

        self.set_child(self.drawing_area)

        Broker.subscribe(ContainersUpdate, self._on_containers_update)

    def _on_containers_update(self, event: ContainersUpdate):
        self.containers = list(event.containers)
        self._recalculate()

    def _recalculate(self):
        # Compute canvas size based on node count
        n_domains = len(set(
            n for c in self.containers for n in (c.networks if c.networks else ["Not connected"])
        ))
        total = len(self.containers) + n_domains
        side = max(MIN_CANVAS, int(math.sqrt(total) * 200))

        self._base_w = side
        self._base_h = side
        self._update_canvas_size()

        self.device_nodes, self.domain_nodes, self.edges = _force_directed_layout(
            self.containers, self._base_w, self._base_h
        )
        self.drawing_area.queue_draw()

    def _update_canvas_size(self):
        self.drawing_area.set_content_width(int(self._base_w * self.zoom_level))
        self.drawing_area.set_content_height(int(self._base_h * self.zoom_level))

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, area, cr, width, height):
        # Background
        cr.set_source_rgb(*COL_BG)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self.device_nodes and not self.domain_nodes:
            cr.set_source_rgba(1, 1, 1, 0.4)
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(16)
            text = "No active devices"
            ext = cr.text_extents(text)
            cr.move_to(width / 2 - ext.width / 2, height / 2)
            cr.show_text(text)
            return

        cr.scale(self.zoom_level, self.zoom_level)

        # 1. Edges
        for dx, dy, dom_x, dom_y, _ in self.edges:
            cr.set_source_rgba(*COL_EDGE)
            cr.set_line_width(1.8)
            cr.move_to(dx, dy)
            cr.line_to(dom_x, dom_y)
            cr.stroke()

        # 2. Domain nodes (collision domains – small green rounded squares)
        cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(10)
        for domain_name, (x, y) in self.domain_nodes.items():
            r = DOMAIN_RADIUS
            _rounded_rect(cr, x - r, y - r, r * 2, r * 2, 5)
            cr.set_source_rgba(*COL_DOMAIN_FILL)
            cr.fill_preserve()
            cr.set_source_rgba(*COL_DOMAIN_BORDER)
            cr.set_line_width(1.5)
            cr.stroke()

            # Label
            cr.set_source_rgb(*COL_DOMAIN_TEXT)
            ext = cr.text_extents(domain_name)
            cr.move_to(x - ext.width / 2, y + r + 14)
            cr.show_text(domain_name)

        # 3. Device nodes (blue circles, orange for end devices)
        cr.set_font_size(11)
        for container, x, y in self.device_nodes:
            is_hover = self.hover_container is not None and self.hover_container == container
            nets = container.networks if container.networks else []
            is_end_device = len(nets) <= 1

            # Glow
            if is_hover:
                cr.set_source_rgba(*(COL_GLOW_END if is_end_device else COL_GLOW))
                cr.arc(x, y, DEVICE_RADIUS + 10, 0, 2 * math.pi)
                cr.fill()

            # Fill
            if is_end_device:
                cr.set_source_rgba(*(COL_ENDDEVICE_HOVER if is_hover else COL_ENDDEVICE))
            else:
                cr.set_source_rgba(*(COL_DEVICE_HOVER if is_hover else COL_DEVICE))
            cr.arc(x, y, DEVICE_RADIUS, 0, 2 * math.pi)
            cr.fill()

            # Border
            cr.set_source_rgba(*(COL_ENDDEVICE_BORDER if is_end_device else COL_DEVICE_BORDER))
            cr.set_line_width(2.0 if is_hover else 1.5)
            cr.arc(x, y, DEVICE_RADIUS, 0, 2 * math.pi)
            cr.stroke()

            # Server icon inside
            cr.set_source_rgba(1, 1, 1, 0.85)
            cr.set_line_width(1.2)
            for dy_off in [-5, 0, 5]:
                cr.move_to(x - 8, y + dy_off)
                cr.line_to(x + 8, y + dy_off)
                cr.stroke()
            for dy_off in [-5, 0, 5]:
                cr.arc(x + 6, y + dy_off, 1.3, 0, 2 * math.pi)
                cr.fill()

            # Label
            cr.set_source_rgb(*COL_TEXT)
            cr.select_font_face("sans-serif", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(15)
            ext = cr.text_extents(container.name)
            cr.move_to(x - ext.width / 2, y - DEVICE_RADIUS - 8)
            cr.show_text(container.name)

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def _to_graph_coords(self, x, y):
        return x / self.zoom_level, y / self.zoom_level

    def _hit_test(self, x, y):
        gx, gy = self._to_graph_coords(x, y)
        for container, nx, ny in self.device_nodes:
            if math.hypot(gx - nx, gy - ny) <= HIT_RADIUS:
                return container
        return None

    def _on_click(self, gesture, n_press, x, y):
        container = self._hit_test(x, y)
        if container is not None:
            Broker.notify(ContainerConnect(container))

    def _on_motion(self, controller, x, y):
        container = self._hit_test(x, y)
        if container != self.hover_container:
            self.hover_container = container
            if container is not None:
                self.drawing_area.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            else:
                self.drawing_area.set_cursor(None)
            self.drawing_area.queue_draw()

    def _on_leave(self, controller):
        if self.hover_container is not None:
            self.hover_container = None
            self.drawing_area.set_cursor(None)
            self.drawing_area.queue_draw()

    def _on_scroll(self, controller, dx, dy):
        # Only zoom when Ctrl is held; otherwise let ScrolledWindow pan
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False  # let the parent ScrolledWindow handle panning
        new_zoom = self.zoom_level - dy * self.ZOOM_STEP
        new_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, new_zoom))
        if new_zoom != self.zoom_level:
            self.zoom_level = new_zoom
            self._update_canvas_size()
            self.drawing_area.queue_draw()
        return True

    def _on_zoom_gesture(self, gesture, scale):
        new_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, scale))
        if new_zoom != self.zoom_level:
            self.zoom_level = new_zoom
            self._update_canvas_size()
            self.drawing_area.queue_draw()


def _rounded_rect(cr, x, y, w, h, r):
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()
