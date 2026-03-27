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

def _force_directed_layout(containers, width, height, iterations=150, yield_frame=None):
    """Compute positions using a force-directed algorithm.

    Nodes = devices (circles) + collision domains (small squares).
    Edges  = device ↔ domain for every network the device belongs to.

    If `yield_frame` is provided, it must be a callable: 
        yield_frame(device_nodes, domain_nodes, edges, description)
    It will be called periodically to save the state for replay mode.

    Returns:
        device_nodes: list of (container, x, y)
        domain_nodes: dict domain_name -> (x, y)
        edges: list of (dev_x, dev_y, dom_x, dom_y, domain_name)
    """
    # --- Initialize variables used by capture ---
    pos_x = []
    pos_y = []
    
    # Helper to capture a frame for replay
    def capture(description):
        if not yield_frame:
            return
        
        c_device_nodes = []
        for ci, c in enumerate(sorted(containers, key=lambda c: c.name)):
            c_device_nodes.append((c, pos_x[ci], pos_y[ci]))

        c_domain_nodes = {}
        # Delay domain_index evaluation mostly using the state later built
        if len(pos_x) >= len(containers):
            # Recalculate domain indexes based on containers
            domain_counts_local = __import__('collections').Counter()
            for c in containers:
                nets = c.networks if c.networks else ["Not connected"]
                domain_counts_local.update(nets)
            local_all_domains = sorted([d for d, count in domain_counts_local.items() if count >= 2])
            local_domain_index = {d: len(containers) + i for i, d in enumerate(local_all_domains)}
            
            for d_name, d_idx in local_domain_index.items():
                if d_idx < len(pos_x):
                    c_domain_nodes[d_name] = (pos_x[d_idx], pos_y[d_idx])

        c_edges = []
        for c, dx, dy in c_device_nodes:
            nets = c.networks if c.networks else ["Not connected"]
            for net in nets:
                if net in c_domain_nodes:
                    dom_x, dom_y = c_domain_nodes[net]
                    c_edges.append((dx, dy, dom_x, dom_y, net))

        yield_frame(c_device_nodes, c_domain_nodes, c_edges, description)

    # --- Initialize variables used by capture ---
    pos_x = []
    pos_y = []
    
    # --- Build the node list ---
    # Collect domains and count devices per domain
    import collections
    domain_counts = collections.Counter()
    for c in containers:
        nets = c.networks if c.networks else ["Not connected"]
        domain_counts.update(nets)

    # Only include domains that have 2 or more devices connected
    all_domains_list = sorted([d for d, count in domain_counts.items() if count >= 2])
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
            if net in domain_index:
                edge_pairs.append((ci, domain_index[net]))

    # Precompute connections for degree-2 domains
    degree2_domains = {}
    for d_name, d_idx in domain_index.items():
        if domain_counts[d_name] == 2:
            connected = [ci for (ci, dj) in edge_pairs if dj == d_idx]
            if len(connected) == 2:
                degree2_domains[d_idx] = (connected[0], connected[1])

    # Precompute neighbors for star repulsion
    neighbors = [[] for _ in range(total)]
    for i, j in edge_pairs:
        neighbors[i].append(j)
        neighbors[j].append(i)

    # Compute device-to-device adjacency (devices sharing a domain)
    device_adj = [set() for _ in range(n_devices)]
    for d_name, d_idx in domain_index.items():
        connected = [ci for (ci, dj) in edge_pairs if dj == d_idx]
        for a in range(len(connected)):
            for b in range(a + 1, len(connected)):
                device_adj[connected[a]].add(connected[b])
                device_adj[connected[b]].add(connected[a])

    # Identify end devices (connected to at most 1 domain)
    end_devices = set()
    for ci, c in enumerate(sorted_containers):
        nets = c.networks if c.networks else []
        if len([n for n in nets if n in domain_index]) <= 1:
            end_devices.add(ci)

    # Community detection via label propagation
    labels = list(range(n_devices))  # each device starts as its own community
    for _ in range(20):
        changed = False
        order = list(range(n_devices))
        rng_lp = random.Random(42)
        rng_lp.shuffle(order)
        for ci in order:
            if not device_adj[ci]:
                continue
            # Count neighbor labels
            label_count = collections.Counter()
            for ni in device_adj[ci]:
                label_count[labels[ni]] += 1
            best_label = label_count.most_common(1)[0][0]
            if labels[ci] != best_label:
                labels[ci] = best_label
                changed = True
        if not changed:
            break

    # Group devices by community label, order communities by size (largest first)
    communities = collections.defaultdict(list)
    for ci in range(n_devices):
        communities[labels[ci]].append(ci)
    sorted_communities = sorted(communities.values(), key=len, reverse=True)

    # --- Initialise positions (community-sector seeding) ---
    margin = 80
    usable_w = max(width - 2 * margin, 200)
    usable_h = max(height - 2 * margin, 200)
    cx, cy = width / 2, height / 2

    pos_x = [0.0] * total
    pos_y = [0.0] * total
    rng = random.Random(42)  # deterministic seed for reproducibility

    # Place each community in its own angular sector
    n_communities = len(sorted_communities)
    sector_start = 0.0
    for comm_idx, comm_devices in enumerate(sorted_communities):
        sector_size = 2 * math.pi * len(comm_devices) / max(n_devices, 1)
        for idx, ci in enumerate(comm_devices):
            angle = sector_start + sector_size * idx / max(len(comm_devices), 1)
            r = min(usable_w, usable_h) * 0.35
            pos_x[ci] = cx + r * math.cos(angle) + rng.uniform(-20, 20)
            pos_y[ci] = cy + r * math.sin(angle) + rng.uniform(-20, 20)
        sector_start += sector_size

    # Place domain nodes at the centroid of their connected devices
    for d_name, d_idx in domain_index.items():
        connected = [ci for (ci, dj) in edge_pairs if dj == d_idx]
        if connected:
            pos_x[d_idx] = sum(pos_x[ci] for ci in connected) / len(connected) + rng.uniform(-10, 10)
            pos_y[d_idx] = sum(pos_y[ci] for ci in connected) / len(connected) + rng.uniform(-10, 10)
        else:
            angle = 2 * math.pi * (d_idx - n_devices) / max(n_domains, 1)
            r = min(usable_w, usable_h) * 0.25
            pos_x[d_idx] = cx + r * math.cos(angle) + rng.uniform(-10, 10)
            pos_y[d_idx] = cy + r * math.sin(angle) + rng.uniform(-10, 10)

    capture("Initial community seeding")

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

        # Device-to-device attractive forces (clustering: devices sharing a domain attract)
        for ci in range(n_devices):
            for ni in device_adj[ci]:
                if ni > ci:
                    dx = pos_x[ci] - pos_x[ni]
                    dy = pos_y[ci] - pos_y[ni]
                    dist = math.sqrt(dx * dx + dy * dy)
                    if dist < 0.01:
                        dist = 0.01
                    force = dist * dist / (k * 4.0)
                    fx = dx / dist * force
                    fy = dy / dist * force
                    disp_x[ci] -= fx
                    disp_y[ci] -= fy
                    disp_x[ni] += fx
                    disp_y[ni] += fy

        # Centrifugal force for end devices: push away from their connected neighbor
        for ci in end_devices:
            # Find the neighbor device(s) through device_adj
            if not device_adj[ci]:
                continue
            # Compute centroid of connected neighbors
            nbr_list = list(device_adj[ci])
            ncx = sum(pos_x[ni] for ni in nbr_list) / len(nbr_list)
            ncy = sum(pos_y[ni] for ni in nbr_list) / len(nbr_list)
            # Push away from neighbor centroid
            dx_r = pos_x[ci] - ncx
            dy_r = pos_y[ci] - ncy
            dist_r = math.sqrt(dx_r * dx_r + dy_r * dy_r)
            if dist_r < 0.01:
                continue
            force_r = k * 0.15
            disp_x[ci] += (dx_r / dist_r) * force_r
            disp_y[ci] += (dy_r / dist_r) * force_r

        # Star repulsion (neighbors of the same node repel each other to distribute edges evenly)
        for i in range(total):
            nbrs = neighbors[i]
            if len(nbrs) > 1:
                for idx1 in range(len(nbrs)):
                    for idx2 in range(idx1 + 1, len(nbrs)):
                        n1 = nbrs[idx1]
                        n2 = nbrs[idx2]
                        dx = pos_x[n1] - pos_x[n2]
                        dy = pos_y[n1] - pos_y[n2]
                        dist2 = dx * dx + dy * dy
                        if dist2 < 0.01:
                            dist2 = 0.01
                            dx = rng.uniform(-0.1, 0.1)
                            dy = rng.uniform(-0.1, 0.1)
                        # Extra strong repulsion between siblings to maximize angle
                        force = (k2 * 3.0) / dist2
                        dist = math.sqrt(dist2)
                        fx = dx / dist * force
                        fy = dy / dist * force
                        disp_x[n1] += fx
                        disp_y[n1] += fy
                        disp_x[n2] -= fx
                        disp_y[n2] -= fy

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

        # Enforce collinearity for degree-2 domains (180 degree angle)
        for d_idx, (c1, c2) in degree2_domains.items():
            pos_x[d_idx] = (pos_x[c1] + pos_x[c2]) / 2.0
            pos_y[d_idx] = (pos_y[c1] + pos_y[c2]) / 2.0

        if iteration % 5 == 0:
            capture(f"FR Simulation: Iteration {iteration}")

        temp -= cool
        
    capture("FR Simulation completed")

    # --- Post-processing: Remove overlaps ---
    clearances = [45.0] * n_devices + [35.0] * n_domains
    for _ in range(50):
        moved = False
        for i in range(total):
            for j in range(i + 1, total):
                dx = pos_x[i] - pos_x[j]
                dy = pos_y[i] - pos_y[j]
                dist2 = dx * dx + dy * dy
                min_dist = clearances[i] + clearances[j]
                if dist2 < min_dist * min_dist:
                    dist = math.sqrt(dist2)
                    if dist < 0.01:
                        dist = 0.01
                        dx = rng.uniform(-0.1, 0.1)
                        dy = rng.uniform(-0.1, 0.1)
                    
                    overlap = min_dist - dist
                    push = overlap * 0.7
                    fx = (dx / dist) * push
                    fy = (dy / dist) * push
                    
                    pos_x[i] += fx
                    pos_y[i] += fy
                    pos_x[j] -= fx
                    pos_y[j] -= fy
                    moved = True
                    
        # Re-enforce collinearity, allowing them to slide along the line to avoid overlaps
        for d_idx, (c1, c2) in degree2_domains.items():
            ax, ay = pos_x[c1], pos_y[c1]
            bx, by = pos_x[c2], pos_y[c2]
            vx, vy = bx - ax, by - ay
            l2 = vx * vx + vy * vy
            
            if l2 > 0.001:
                dx, dy = pos_x[d_idx], pos_y[d_idx]
                t = ((dx - ax) * vx + (dy - ay) * vy) / l2
                l = math.sqrt(l2)
                margin_t = min(0.45, 45.0 / l)
                t = max(margin_t, min(1.0 - margin_t, t))
                pos_x[d_idx] = ax + t * vx
                pos_y[d_idx] = ay + t * vy
            else:
                pos_x[d_idx] = ax
                pos_y[d_idx] = ay
            
        for i in range(total):
            pos_x[i] = max(margin, min(width - margin, pos_x[i]))
            pos_y[i] = max(margin, min(height - margin, pos_y[i]))
            
        if not moved:
            break
    capture("Overlap removal pass 1 completed")

    # --- Post-processing helpers ---
    def _segments_cross(ax, ay, bx, by, cx_, cy_, dx_, dy_):
        """Return True if segment (a→b) strictly crosses segment (c→d)."""
        def _cross2d(ox, oy, px, py, qx, qy):
            return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)
        d1 = _cross2d(cx_, cy_, dx_, dy_, ax, ay)
        d2 = _cross2d(cx_, cy_, dx_, dy_, bx, by)
        d3 = _cross2d(ax, ay, bx, by, cx_, cy_)
        d4 = _cross2d(ax, ay, bx, by, dx_, dy_)
        return ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
               ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0))

    def _build_virtual_segments():
        segs = []
        consumed = set()
        for d_idx, (c1, c2) in degree2_domains.items():
            consumed.add((c1, d_idx))
            consumed.add((c2, d_idx))
            segs.append((pos_x[c1], pos_y[c1],
                         pos_x[c2], pos_y[c2], c1, c2))
        for ci, dj in edge_pairs:
            if (ci, dj) not in consumed:
                segs.append((pos_x[ci], pos_y[ci],
                             pos_x[dj], pos_y[dj], ci, -1))
        return segs

    def _count_all_crossings(segs):
        count = 0
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                s1, s2 = segs[i], segs[j]
                if s1[4] == s2[4] or s1[4] == s2[5] or \
                   s1[5] == s2[4] or (s1[5] == s2[5] and s1[5] != -1):
                    continue
                if _segments_cross(s1[0], s1[1], s1[2], s1[3],
                                   s2[0], s2[1], s2[2], s2[3]):
                    count += 1
        return count

    # --- Post-processing: Swap end devices whose segments cross ---
    end_device_list = sorted(end_devices)

    if len(end_device_list) >= 2:
        improved = True
        for _ in range(5):
            if not improved:
                break
            improved = False
            segs = _build_virtual_segments()
            current_crossings = _count_all_crossings(segs)

            crossing_pairs = []
            for i in range(len(segs)):
                for j in range(i + 1, len(segs)):
                    s1, s2 = segs[i], segs[j]
                    if s1[4] == s2[4] or s1[4] == s2[5] or \
                       s1[5] == s2[4] or (s1[5] == s2[5] and s1[5] != -1):
                        continue
                    # Check if this end device is part of one of the crossing segments
                    ends_in_s1 = [n for n in (s1[4], s1[5]) if n in end_devices]
                    ends_in_s2 = [n for n in (s2[4], s2[5]) if n in end_devices]
                    if not ends_in_s1 and not ends_in_s2: # Only interested in crossings involving end devices
                        continue
                    if not _segments_cross(s1[0], s1[1], s1[2], s1[3],
                                           s2[0], s2[1], s2[2], s2[3]):
                        continue
                    
                    # Find end devices involved in this crossing
                    for ei in ends_in_s1:
                        for ej in ends_in_s2:
                            pair = (min(ei, ej), max(ei, ej))
                            if pair not in crossing_pairs:
                                crossing_pairs.append(pair)

            for ei, ej in crossing_pairs:
                pos_x[ei], pos_x[ej] = pos_x[ej], pos_x[ei]
                pos_y[ei], pos_y[ej] = pos_y[ej], pos_y[ei]

                for d_idx, (c1, c2) in degree2_domains.items():
                    if c1 in (ei, ej) or c2 in (ei, ej):
                        pos_x[d_idx] = (pos_x[c1] + pos_x[c2]) / 2.0
                        pos_y[d_idx] = (pos_y[c1] + pos_y[c2]) / 2.0

                new_segs = _build_virtual_segments()
                new_crossings = _count_all_crossings(new_segs)

                if new_crossings < current_crossings:
                    current_crossings = new_crossings
                    segs = new_segs
                    improved = True
                else:
                    pos_x[ei], pos_x[ej] = pos_x[ej], pos_x[ei]
                    pos_y[ei], pos_y[ej] = pos_y[ej], pos_y[ei]
                    for d_idx, (c1, c2) in degree2_domains.items():
                        if c1 in (ei, ej) or c2 in (ei, ej):
                            pos_x[d_idx] = (pos_x[c1] + pos_x[c2]) / 2.0
                            pos_y[d_idx] = (pos_y[c1] + pos_y[c2]) / 2.0
            
            if improved:
                capture(f"End Device Swap pass completed")

    # --- Post-processing: Angular repositioning of crossing end devices ---

    for ed in end_device_list:
        # Check if this end device's virtual segment crosses any other segment
        segs = _build_virtual_segments()
        ed_has_crossing = False
        for i in range(len(segs)):
            for j in range(i + 1, len(segs)):
                s1, s2 = segs[i], segs[j]
                if s1[4] == s2[4] or s1[4] == s2[5] or \
                   s1[5] == s2[4] or (s1[5] == s2[5] and s1[5] != -1):
                    continue
                # Check if this end device is part of one of the crossing segments
                if ed not in (s1[4], s1[5], s2[4], s2[5]):
                    continue
                if _segments_cross(s1[0], s1[1], s1[2], s1[3],
                                   s2[0], s2[1], s2[2], s2[3]):
                    ed_has_crossing = True
                    break
            if ed_has_crossing:
                break

        if not ed_has_crossing:
            continue

        # Find the hub device this end device connects to
        hub_ci = None
        for d_idx, (c1, c2) in degree2_domains.items():
            if c1 == ed:
                hub_ci = c2
                break
            elif c2 == ed:
                hub_ci = c1
                break

        if hub_ci is None:
            # End device connected to a domain with degree > 2: find via edge_pairs
            for ci, dj in edge_pairs:
                if ci == ed:
                    # Find other devices on this domain
                    for ci2, dj2 in edge_pairs:
                        if dj2 == dj and ci2 != ed:
                            hub_ci = ci2
                            break
                    break
        if hub_ci is None:
            continue

        hx, hy = pos_x[hub_ci], pos_y[hub_ci]

        # Compute angles of ALL connections from the hub (neighbors in the graph)
        # excluding the connection to this end device
        hub_angles = []
        for ni in neighbors[hub_ci]:
            # ni can be a domain node; get all connected nodes through it
            nx, ny = pos_x[ni], pos_y[ni]
            angle = math.atan2(ny - hy, nx - hx)
            # Skip if this neighbor leads to our end device
            if ni < n_devices and ni == ed:
                continue
            # If ni is a domain, check if it only connects to hub and ed
            if ni >= n_devices:
                is_ed_domain = False
                for d_idx, (c1, c2) in degree2_domains.items():
                    if d_idx == ni and (c1 == ed or c2 == ed):
                        is_ed_domain = True
                        break
                if is_ed_domain:
                    continue
            hub_angles.append(angle)

        if not hub_angles:
            continue

        # Sort angles, find the largest gap
        hub_angles.sort()
        best_gap = -1.0
        best_mid_angle = hub_angles[0]  # default

        for i in range(len(hub_angles)):
            a1 = hub_angles[i]
            a2 = hub_angles[(i + 1) % len(hub_angles)]
            gap = a2 - a1
            if i == len(hub_angles) - 1:
                gap = (a2 + 2 * math.pi) - a1  # wrap-around gap
            if gap > best_gap:
                best_gap = gap
                best_mid_angle = a1 + gap / 2.0

        # Save old position for rollback
        old_x, old_y = pos_x[ed], pos_y[ed]

        # Place the domain and the end device at the midpoint of the largest gap
        # Domain goes first (closest to hub), then end device
        ed_domain = None
        for d_idx, (c1, c2) in degree2_domains.items():
            if c1 == ed or c2 == ed:
                ed_domain = d_idx
                break

        if ed_domain is not None:
            # Place domain
            dom_dist = clearances[hub_ci] + clearances[ed_domain]
            pos_x[ed_domain] = hx + dom_dist * math.cos(best_mid_angle)
            pos_y[ed_domain] = hy + dom_dist * math.sin(best_mid_angle)
            
            # Place end device directly behind domain
            ed_dist = dom_dist + clearances[ed_domain] + clearances[ed]
            pos_x[ed] = hx + ed_dist * math.cos(best_mid_angle)
            pos_y[ed] = hy + ed_dist * math.sin(best_mid_angle)
        else:
            # Fallback if no domain (direct connection, rare)
            place_dist = clearances[ed] + clearances[hub_ci]
            pos_x[ed] = hx + place_dist * math.cos(best_mid_angle)
            pos_y[ed] = hy + place_dist * math.sin(best_mid_angle)

        # Keep within bounds
        pos_x[ed] = max(margin, min(width - margin, pos_x[ed]))
        pos_y[ed] = max(margin, min(height - margin, pos_y[ed]))

        # Only re-enforce collinearity if we didn't explicitly place the domain above
        if ed_domain is None:
            for d_idx, (c1, c2) in degree2_domains.items():
                if c1 == ed or c2 == ed:
                    pos_x[d_idx] = (pos_x[c1] + pos_x[c2]) / 2.0
                    pos_y[d_idx] = (pos_y[c1] + pos_y[c2]) / 2.0

        # Verify improvement: only keep if crossings strictly decrease
        new_segs = _build_virtual_segments()
        old_segs_count = _count_all_crossings(segs)
        new_segs_count = _count_all_crossings(new_segs)
        if new_segs_count >= old_segs_count:
            # Revert
            pos_x[ed], pos_y[ed] = old_x, old_y
            for d_idx, (c1, c2) in degree2_domains.items():
                if c1 == ed or c2 == ed:
                    pos_x[d_idx] = (pos_x[c1] + pos_x[c2]) / 2.0
                    pos_y[d_idx] = (pos_y[c1] + pos_y[c2]) / 2.0
    capture("Angular repositioning pass completed")

    # Small overlap removal pass after angular repositioning
    for _ in range(20):
        moved = False
        for i in range(total):
            for j in range(i + 1, total):
                dx = pos_x[i] - pos_x[j]
                dy = pos_y[i] - pos_y[j]
                dist2 = dx * dx + dy * dy
                min_dist = clearances[i] + clearances[j]
                if dist2 < min_dist * min_dist:
                    dist = math.sqrt(dist2)
                    if dist < 0.01:
                        dist = 0.01
                        dx = rng.uniform(-0.1, 0.1)
                        dy = rng.uniform(-0.1, 0.1)
                    overlap = min_dist - dist
                    push = overlap * 0.5
                    fx = (dx / dist) * push
                    fy = (dy / dist) * push
                    pos_x[i] += fx
                    pos_y[i] += fy
                    pos_x[j] -= fx
                    pos_y[j] -= fy
                    moved = True
        # Re-enforce collinearity
        for d_idx, (c1, c2) in degree2_domains.items():
            ax, ay = pos_x[c1], pos_y[c1]
            bx, by = pos_x[c2], pos_y[c2]
            vx, vy = bx - ax, by - ay
            l2 = vx * vx + vy * vy
            if l2 > 0.001:
                dx, dy = pos_x[d_idx], pos_y[d_idx]
                t = ((dx - ax) * vx + (dy - ay) * vy) / l2
                l = math.sqrt(l2)
                margin_t = min(0.45, 45.0 / l)
                t = max(margin_t, min(1.0 - margin_t, t))
                pos_x[d_idx] = ax + t * vx
                pos_y[d_idx] = ay + t * vy
            else:
                pos_x[d_idx] = ax
                pos_y[d_idx] = ay
        for i in range(total):
            pos_x[i] = max(margin, min(width - margin, pos_x[i]))
            pos_y[i] = max(margin, min(height - margin, pos_y[i]))
        if not moved:
            break

    capture("Final angular repositioning and cleanup")

    # --- Build results ---
    device_nodes = []
    for ci, c in enumerate(sorted_containers):
        device_nodes.append((c, pos_x[ci], pos_y[ci]))

    domain_nodes = {}
    for d_name, d_idx in domain_index.items():
        domain_nodes[d_name] = (pos_x[d_idx], pos_y[d_idx])

    edges = []
    # Identify degree-2 domains and their connected device pairs
    degree2_device_pairs = {}  # domain_name -> (container_a, container_b)
    for d_name, d_idx in domain_index.items():
        if domain_counts[d_name] == 2:
            connected = [ci for (ci, dj) in edge_pairs if dj == d_idx]
            if len(connected) == 2:
                ca, cb = sorted_containers[connected[0]], sorted_containers[connected[1]]
                degree2_device_pairs[d_name] = (ca, cb)
    
    dev_pos = {c: (x, y) for c, x, y in device_nodes}
    for d_name, (ca, cb) in degree2_device_pairs.items():
        ax, ay = dev_pos[ca]
        bx, by = dev_pos[cb]
        # Single device-to-device edge
        edges.append((ax, ay, bx, by, d_name))
        # Place domain at midpoint
        domain_nodes[d_name] = ((ax + bx) / 2, (ay + by) / 2)
    
    # Non-degree-2 domains: normal device→domain edges
    for c, dx, dy in device_nodes:
        nets = c.networks if c.networks else ["Not connected"]
        for net in nets:
            if net in domain_nodes and net not in degree2_device_pairs:
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


from gi.repository import Gtk, Gdk, GLib, Adw

class NetworkGraphView(Gtk.ScrolledWindow):
    """A scrollable network topology graph view drawn with Cairo (GNS3-style), with Replay Mode."""

    ZOOM_MIN = 0.3
    ZOOM_MAX = 3.0
    ZOOM_STEP = 0.1
    BG_DARK = (0.11, 0.11, 0.13)
    BG_LIGHT = (0.96, 0.96, 0.97)
    THEME_COLORS = {
        True: {
            "edge": COL_EDGE,
            "device": COL_DEVICE,
            "device_hover": COL_DEVICE_HOVER,
            "device_border": COL_DEVICE_BORDER,
            "enddevice": COL_ENDDEVICE,
            "enddevice_hover": COL_ENDDEVICE_HOVER,
            "enddevice_border": COL_ENDDEVICE_BORDER,
            "glow": COL_GLOW,
            "glow_end": COL_GLOW_END,
            "domain_fill": COL_DOMAIN_FILL,
            "domain_border": COL_DOMAIN_BORDER,
            "domain_text": COL_DOMAIN_TEXT,
            "text": COL_TEXT,
            "icon": (1.0, 1.0, 1.0, 0.85),
        },
        False: {
            # Light mode palette tuned for stronger contrast on a bright canvas.
            "edge": (0.26, 0.42, 0.62, 0.42),
            "device": (0.15, 0.36, 0.72, 0.95),
            "device_hover": (0.10, 0.30, 0.64, 0.98),
            "device_border": (0.06, 0.20, 0.44, 1.0),
            "enddevice": (0.83, 0.46, 0.12, 0.95),
            "enddevice_hover": (0.74, 0.38, 0.08, 0.98),
            "enddevice_border": (0.55, 0.24, 0.04, 1.0),
            "glow": (0.14, 0.36, 0.70, 0.16),
            "glow_end": (0.72, 0.40, 0.10, 0.18),
            "domain_fill": (0.20, 0.58, 0.33, 0.92),
            "domain_border": (0.08, 0.38, 0.20, 1.0),
            "domain_text": (0.08, 0.28, 0.14),
            "text": (0.08, 0.08, 0.10),
            "icon": (1.0, 1.0, 1.0, 0.92),
        },
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, vexpand=True, hexpand=True)

        self.containers: list[Container] = []
        self.device_nodes = []
        self.domain_nodes = {}
        self.edges = []
        
        # Replay State
        self.replay_frames = []      # list of dicts: {'device_nodes': [], 'domain_nodes': {}, 'edges': [], 'desc': str}
        self.current_frame_idx = 0
        self.is_playing = False
        self.animator_id = None
        self.play_speed_ms = 100

        self.hover_container = None
        self.zoom_level = 1.0
        self._base_w = MIN_CANVAS
        self._base_h = MIN_CANVAS

        # Drag state
        self._drag_node = None     # ('device', container) or ('domain', name)
        self._drag_offset_x = 0.0  # offset of cursor from node center at drag start
        self._drag_offset_y = 0.0
        self._is_dragging = False  # True once the mouse has moved significantly
        self._suppress_click = False  # Suppress the next click after a drag
        self._style_manager = Adw.StyleManager.get_default()

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
        self.drawing_area.add_controller(zoom_gesture)

        # Drag nodes
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.drawing_area.add_controller(drag)

        # Overlay to hold the replay toolbar at the bottom
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(self.drawing_area)
        
        self.replay_toolbar = self._build_replay_ui()
        self.overlay.add_overlay(self.replay_toolbar)

        self.set_child(self.overlay)
        self._style_manager.connect("notify::dark", self._on_theme_changed)

        Broker.subscribe(ContainersUpdate, self._on_containers_update)

    def _on_theme_changed(self, *_):
        self.drawing_area.queue_draw()

    def _build_replay_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_halign(Gtk.Align.FILL)
        box.set_valign(Gtk.Align.END)
        box.set_margin_bottom(20)
        box.set_margin_start(40)
        box.set_margin_end(40)
        box.add_css_class("osd") # Gives it a nice floating panel look
        
        # Controls
        self.btn_prev = Gtk.Button(icon_name="media-skip-backward-symbolic")
        self.btn_prev.connect("clicked", lambda _: self._step(-1))
        
        self.btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        self.btn_play.connect("clicked", self._toggle_play)
        
        self.btn_next = Gtk.Button(icon_name="media-skip-forward-symbolic")
        self.btn_next.connect("clicked", lambda _: self._step(1))
        
        self.btn_fast = Gtk.ToggleButton(label="Fast") # Toggle fast mode
        self.btn_fast.connect("toggled", self._toggle_fast)
        
        self.lbl_desc = Gtk.Label(label="Calculating layout...")
        self.lbl_desc.set_width_chars(35)
        self.lbl_desc.set_ellipsize(Pango.EllipsizeMode.END) if hasattr(Gtk, 'Pango') else None
        
        self.slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 1)
        self.slider.set_draw_value(False)
        self.slider.set_hexpand(True)
        self.slider.set_size_request(400, -1)
        self.slider.connect("value-changed", self._on_slider_changed)
        
        # Pack
        box.append(self.btn_prev)
        box.append(self.btn_play)
        box.append(self.btn_next)
        box.append(self.btn_fast)
        box.append(self.slider)
        box.append(self.lbl_desc)
        
        # Hide toolbar until recalculate finishes
        box.set_visible(False)
        return box

    def _toggle_play(self, btn):
        if not self.replay_frames:
            return
        self.is_playing = not self.is_playing
        if self.is_playing:
            self.btn_play.set_icon_name("media-playback-pause-symbolic")
            # If at the end, restart
            if self.current_frame_idx >= len(self.replay_frames) - 1:
                self.current_frame_idx = 0
            if self.animator_id is None:
                self.animator_id = GLib.timeout_add(self.play_speed_ms, self._animator)
        else:
            self.btn_play.set_icon_name("media-playback-start-symbolic")
            if self.animator_id is not None:
                GLib.source_remove(self.animator_id)
                self.animator_id = None

    def _toggle_fast(self, btn):
        self.play_speed_ms = 20 if btn.get_active() else 100
        if self.is_playing and self.animator_id is not None:
            GLib.source_remove(self.animator_id)
            self.animator_id = GLib.timeout_add(self.play_speed_ms, self._animator)

    def _step(self, direction):
        if not self.replay_frames: return
        # Pause playback on manual step
        if self.is_playing:
            self._toggle_play(self.btn_play)
            
        new_idx = max(0, min(len(self.replay_frames) - 1, self.current_frame_idx + direction))
        self.slider.set_value(new_idx)

    def _on_slider_changed(self, scale):
        if not self.replay_frames: return
        self.current_frame_idx = int(scale.get_value())
        frame = self.replay_frames[self.current_frame_idx]
        self.device_nodes = frame['device_nodes']
        self.domain_nodes = frame['domain_nodes']
        self.edges = frame['edges']
        self.lbl_desc.set_text(frame['desc'])
        self.drawing_area.queue_draw()

    def _animator(self):
        if not self.is_playing or not self.replay_frames:
            self.animator_id = None
            return GLib.SOURCE_REMOVE
            
        if self.current_frame_idx < len(self.replay_frames) - 1:
            self.slider.set_value(self.current_frame_idx + 1)
            return GLib.SOURCE_CONTINUE
        else:
            self._toggle_play(self.btn_play) # Pause at end
            return GLib.SOURCE_REMOVE

    def _on_containers_update(self, event: ContainersUpdate):
        new_containers = list(event.containers)
        # Check if the container set actually changed (by name set)
        old_names = {c.name for c in self.containers}
        new_names = {c.name for c in new_containers}
        self.containers = new_containers
        if old_names != new_names:
            self._recalculate()
        else:
            # Same containers - just update the Container refs without recalculating
            name_to_new = {c.name: c for c in new_containers}
            self.device_nodes = [
                (name_to_new[c.name], x, y) if c.name in name_to_new else (c, x, y)
                for c, x, y in self.device_nodes
            ]
            self._rebuild_edges()
            self.drawing_area.queue_draw()

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

        # Reset replay state
        self.replay_frames = []
        if self.animator_id is not None:
            GLib.source_remove(self.animator_id)
            self.animator_id = None
        self.is_playing = False
        self.btn_play.set_icon_name("media-playback-start-symbolic")
        self.replay_toolbar.set_visible(False)

        def _on_frame(d_nodes, dom_nodes, edgs, desc):
            self.replay_frames.append({
                'device_nodes': d_nodes,
                'domain_nodes': dom_nodes,
                'edges': edgs,
                'desc': desc
            })

        # Run layout synchronously and capture frames
        debug_replay = __import__('os').environ.get('KATHARSIS_DEBUG_REPLAY') == '1'
        
        if debug_replay:
            _force_directed_layout(
                self.containers, self._base_w, self._base_h, yield_frame=_on_frame
            )
            if self.replay_frames:
                self.slider.set_range(0, len(self.replay_frames) - 1)
                self.slider.set_value(len(self.replay_frames) - 1) # Jump to final
                self.replay_toolbar.set_visible(True)
                self.drawing_area.queue_draw()
        else:
            d_nodes, dom_nodes, edgs = _force_directed_layout(
                self.containers, self._base_w, self._base_h
            )
            self.device_nodes = d_nodes
            self.domain_nodes = dom_nodes
            self.edges = edgs
            self.drawing_area.queue_draw()

    def _update_canvas_size(self):
        self.drawing_area.set_content_width(int(self._base_w * self.zoom_level))
        self.drawing_area.set_content_height(int(self._base_h * self.zoom_level))

    def reset_layout(self):
        """Clear user-adjusted positions and recalculate the layout from scratch."""
        self.device_nodes.clear()
        self.domain_nodes.clear()
        self.edges.clear()
        self._recalculate()

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self, area, cr, width, height):
        # Background
        is_dark = self._style_manager.get_dark()
        theme = self.THEME_COLORS[is_dark]
        bg = self.BG_DARK if is_dark else self.BG_LIGHT
        cr.set_source_rgb(*bg)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if not self.device_nodes and not self.domain_nodes:
            if is_dark:
                cr.set_source_rgba(1, 1, 1, 0.4)
            else:
                cr.set_source_rgba(0.1, 0.1, 0.1, 0.55)
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
            cr.set_source_rgba(*theme["edge"])
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
            cr.set_source_rgba(*theme["domain_fill"])
            cr.fill_preserve()
            cr.set_source_rgba(*theme["domain_border"])
            cr.set_line_width(1.5)
            cr.stroke()

            # Label
            cr.set_source_rgb(*theme["domain_text"])
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
                cr.set_source_rgba(*(theme["glow_end"] if is_end_device else theme["glow"]))
                cr.arc(x, y, DEVICE_RADIUS + 10, 0, 2 * math.pi)
                cr.fill()

            # Fill
            if is_end_device:
                cr.set_source_rgba(*(theme["enddevice_hover"] if is_hover else theme["enddevice"]))
            else:
                cr.set_source_rgba(*(theme["device_hover"] if is_hover else theme["device"]))
            cr.arc(x, y, DEVICE_RADIUS, 0, 2 * math.pi)
            cr.fill()

            # Border
            cr.set_source_rgba(*(theme["enddevice_border"] if is_end_device else theme["device_border"]))
            cr.set_line_width(2.0 if is_hover else 1.5)
            cr.arc(x, y, DEVICE_RADIUS, 0, 2 * math.pi)
            cr.stroke()

            # Server icon inside
            cr.set_source_rgba(*theme["icon"])
            cr.set_line_width(1.2)
            for dy_off in [-5, 0, 5]:
                cr.move_to(x - 8, y + dy_off)
                cr.line_to(x + 8, y + dy_off)
                cr.stroke()
            for dy_off in [-5, 0, 5]:
                cr.arc(x + 6, y + dy_off, 1.3, 0, 2 * math.pi)
                cr.fill()

            # Label
            cr.set_source_rgb(*theme["text"])
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
        """Return the Container under screen coords (x, y), or None."""
        gx, gy = self._to_graph_coords(x, y)
        for container, nx, ny in self.device_nodes:
            if math.hypot(gx - nx, gy - ny) <= HIT_RADIUS:
                return container
        return None

    def _on_click(self, gesture, n_press, x, y):
        if self._suppress_click:
            self._suppress_click = False
            return  # Suppress click after a drag
        container = self._hit_test_device(x, y)
        if container is not None:
            Broker.notify(ContainerConnect(container))

    def _on_motion(self, controller, x, y):
        # Track mouse position for scroll-zoom centering
        self._last_mouse_x = x
        self._last_mouse_y = y
        if self._is_dragging:
            return  # let drag handler own the cursor
        container = self._hit_test_device(x, y)
        if container != self.hover_container:
            self.hover_container = container
            if container is not None:
                self.drawing_area.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            else:
                # Check if we are over a domain (show move cursor)
                if self._hit_test_domain(x, y) is not None:
                    self.drawing_area.set_cursor(Gdk.Cursor.new_from_name("move"))
                else:
                    self.drawing_area.set_cursor(None)
            self.drawing_area.queue_draw()

    def _on_leave(self, controller):
        if self.hover_container is not None:
            self.hover_container = None
            self.drawing_area.set_cursor(None)
            self.drawing_area.queue_draw()

    # ------------------------------------------------------------------
    # Drag handling
    # ------------------------------------------------------------------

    def _hit_test_device(self, x, y):
        """Return the Container under screen coords (x, y), or None."""
        gx, gy = self._to_graph_coords(x, y)
        for container, nx, ny in self.device_nodes:
            if math.hypot(gx - nx, gy - ny) <= HIT_RADIUS:
                return container
        return None

    def _hit_test_domain(self, x, y):
        """Return the domain name under screen coords (x, y), or None."""
        gx, gy = self._to_graph_coords(x, y)
        for name, (nx, ny) in self.domain_nodes.items():
            if math.hypot(gx - nx, gy - ny) <= DOMAIN_RADIUS + 8:
                return name
        return None

    def _rebuild_edges(self):
        """Rebuild self.edges from current device_nodes and domain_nodes positions.
        
        For degree-2 domains (connecting exactly 2 devices), creates a single
        device-to-device edge and repositions the domain to the midpoint.
        """
        dev_pos = {c: (x, y) for c, x, y in self.device_nodes}
        
        # Identify degree-2 domains
        domain_device_map = {}  # domain_name -> list of containers connected to it
        for container, _, _ in self.device_nodes:
            nets = container.networks if container.networks else []
            for net in nets:
                if net in self.domain_nodes:
                    domain_device_map.setdefault(net, []).append(container)
        
        degree2_domains = {}  # domain_name -> (container_a, container_b)
        for d_name, devs in domain_device_map.items():
            if len(devs) == 2:
                degree2_domains[d_name] = (devs[0], devs[1])
        
        new_edges = []
        # Degree-2 domains: single device-to-device edge, domain at midpoint
        for d_name, (ca, cb) in degree2_domains.items():
            ax, ay = dev_pos[ca]
            bx, by = dev_pos[cb]
            new_edges.append((ax, ay, bx, by, d_name))
            self.domain_nodes[d_name] = ((ax + bx) / 2, (ay + by) / 2)
        
        # Non-degree-2 domains: normal device→domain edges
        for container, dx, dy in self.device_nodes:
            nets = container.networks if container.networks else []
            for net in nets:
                if net in self.domain_nodes and net not in degree2_domains:
                    dom_x, dom_y = self.domain_nodes[net]
                    new_edges.append((dx, dy, dom_x, dom_y, net))
        
        self.edges = new_edges

    def _on_drag_begin(self, gesture, start_x, start_y):
        self._is_dragging = False
        # Determine what we're dragging
        container = self._hit_test_device(start_x, start_y)
        if container is not None:
            gx, gy = self._to_graph_coords(start_x, start_y)
            # Find device position
            for c, cx, cy in self.device_nodes:
                if c == container:
                    self._drag_node = ('device', container)
                    self._drag_offset_x = cx - gx
                    self._drag_offset_y = cy - gy
                    break
            return
        domain = self._hit_test_domain(start_x, start_y)
        if domain is not None:
            gx, gy = self._to_graph_coords(start_x, start_y)
            nx, ny = self.domain_nodes[domain]
            self._drag_node = ('domain', domain)
            self._drag_offset_x = nx - gx
            self._drag_offset_y = ny - gy
            return
        self._drag_node = None

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._drag_node is None:
            return
        # Mark as "actually moved"
        if not self._is_dragging and (abs(offset_x) > 4 or abs(offset_y) > 4):
            self._is_dragging = True
            self.drawing_area.set_cursor(Gdk.Cursor.new_from_name("grabbing"))
        if not self._is_dragging:
            return

        # Current drag position in graph coordinates
        start_x, start_y = gesture.get_start_point()[1], gesture.get_start_point()[2]
        cur_x = start_x + offset_x
        cur_y = start_y + offset_y
        gx, gy = self._to_graph_coords(cur_x, cur_y)
        new_x = gx + self._drag_offset_x
        new_y = gy + self._drag_offset_y

        node_type, node_id = self._drag_node
        if node_type == 'device':
            self.device_nodes = [
                (c, new_x, new_y) if c == node_id else (c, cx, cy)
                for c, cx, cy in self.device_nodes
            ]
        elif node_type == 'domain':
            self.domain_nodes[node_id] = (new_x, new_y)

        self._rebuild_edges()
        self.drawing_area.queue_draw()

    def _on_drag_end(self, gesture, offset_x, offset_y):
        if self._is_dragging:
            self._suppress_click = True  # Will suppress the next click event
            self._is_dragging = False
            self.drawing_area.set_cursor(None)
        self._drag_node = None

    def _on_scroll(self, controller, dx, dy):
        # Only zoom when Ctrl is held; otherwise let ScrolledWindow pan
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False  # let the parent ScrolledWindow handle panning

        new_zoom = self.zoom_level - dy * self.ZOOM_STEP
        new_zoom = max(self.ZOOM_MIN, min(self.ZOOM_MAX, new_zoom))
        if new_zoom == self.zoom_level:
            return True

        # Get mouse position relative to the drawing area (widget coords)
        # Use the last known motion position tracked via _on_motion
        mouse_x = getattr(self, '_last_mouse_x', 0.0)
        mouse_y = getattr(self, '_last_mouse_y', 0.0)

        # Current scroll offsets
        hadj = self.get_hadjustment()
        vadj = self.get_vadjustment()
        scroll_x = hadj.get_value()
        scroll_y = vadj.get_value()

        # Graph-space point under cursor before zoom
        graph_x = (scroll_x + mouse_x) / self.zoom_level
        graph_y = (scroll_y + mouse_y) / self.zoom_level

        # Apply new zoom
        self.zoom_level = new_zoom
        self._update_canvas_size()

        # After zoom, adjust scroll so the same graph-space point stays under cursor
        new_scroll_x = graph_x * self.zoom_level - mouse_x
        new_scroll_y = graph_y * self.zoom_level - mouse_y
        hadj.set_value(max(0, new_scroll_x))
        vadj.set_value(max(0, new_scroll_y))

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
