"""Route mode: vines that travel through the waypoints in order.

1. The waypoints are joined by the shortest path along the body surface
   (Dijkstra on a surface graph), so the route goes over a shoulder instead of
   through the chest.
2. Several strands follow the route, twisting around it like a rope. Their
   offset is rotated around the route tangent; the parts that would end up
   inside the body are pushed back out, so around a thin limb the strands
   really spiral around it while on the torso they stay on the near side.
3. The waypoint ranges set the width of the bundle along the way; some
   strands lift off into the air; side shoots, aerial shoots and tendrils
   are added along the strands.
"""

import heapq
import math

from mathutils import Matrix, Vector, noise

from . import colonize, surface

UP = Vector((0.0, 0.0, 1.0))


def _dijkstra_path(nodes, edges, s, t):
    adj = [[] for _ in nodes]
    for a, b in edges:
        d = (nodes[a].co - nodes[b].co).length
        adj[a].append((b, d))
        adj[b].append((a, d))
    dist = {s: 0.0}
    prev = {}
    heap = [(0.0, s)]
    while heap:
        d, i = heapq.heappop(heap)
        if i == t:
            break
        if d > dist.get(i, math.inf):
            continue
        for j, w in adj[i]:
            nd = d + w
            if nd < dist.get(j, math.inf):
                dist[j] = nd
                prev[j] = i
                heapq.heappush(heap, (nd, j))
    if t not in dist:
        return None
    path = [t]
    while path[-1] != s:
        path.append(prev[path[-1]])
    return list(reversed(path))


def _resample(pts, step):
    lens = [0.0]
    for i in range(1, len(pts)):
        lens.append(lens[-1] + (pts[i] - pts[i - 1]).length)
    total = lens[-1]
    if total < 1e-9:
        return list(pts), [0.0] * len(pts)
    n = max(2, int(math.ceil(total / step)) + 1)
    out, ss = [], []
    j = 0
    for k in range(n):
        s = total * k / (n - 1)
        while j < len(lens) - 2 and lens[j + 1] < s:
            j += 1
        seg = lens[j + 1] - lens[j]
        u = 0.0 if seg < 1e-12 else (s - lens[j]) / seg
        out.append(pts[j].lerp(pts[j + 1], u))
        ss.append(s)
    return out, ss


def build_route(sampler, waypoints, widths, P, scale, rng):
    """Surface route through the waypoints -> (points, normals, arc lengths, widths per point)."""
    spacing = max(P.attractor_spacing * scale, 0.006 * scale)
    locs = [h.loc for h in waypoints]
    if len(locs) == 1:
        return None
    span = max((locs[i + 1] - locs[i]).length for i in range(len(locs) - 1))
    reach = span * 0.75 + max(widths) * 2.0
    nodes = surface.scatter_nodes(sampler, locs, reach, spacing, rng)
    pts = []
    if len(nodes) >= 4:
        edges = surface.connect(sampler, nodes, spacing)
        ids = [surface.nearest_node(nodes, p) for p in locs]
        for a, b in zip(ids[:-1], ids[1:]):
            seg = _dijkstra_path(nodes, edges, a, b) if a != b else [a]
            if seg is None:  # disconnected surface: straight hop
                seg_pts = [nodes[a].co, nodes[b].co]
            else:
                seg_pts = [nodes[i].co for i in seg]
            if pts:
                seg_pts = seg_pts[1:]
            pts += seg_pts
    if len(pts) < 2:
        pts = list(locs)
    pts[0], pts[-1] = locs[0], locs[-1]
    # Smooth out the zig-zag of the graph path and keep it on the surface.
    for _ in range(6):
        pts = [pts[0]] + [(pts[k - 1] + pts[k] * 2.0 + pts[k + 1]) * 0.25 for k in range(1, len(pts) - 1)] + [pts[-1]]
        pts = [sampler.nearest(p).loc for p in pts]
    step = P.step * scale
    pts, ss = _resample(pts, step)
    hits = [sampler.nearest(p) for p in pts]
    normals = [h.normal for h in hits]
    pts = [h.loc for h in hits]
    # Width along the route: interpolate the waypoint ranges by nearest arc position.
    wp_s = []
    for p in locs:
        k = min(range(len(pts)), key=lambda i: (pts[i] - p).length_squared)
        wp_s.append(ss[k])
    order = sorted(zip(wp_s, widths))
    ws = []
    for s in ss:
        if s <= order[0][0]:
            ws.append(order[0][1])
        elif s >= order[-1][0]:
            ws.append(order[-1][1])
        else:
            for (s0, w0), (s1, w1) in zip(order[:-1], order[1:]):
                if s0 <= s <= s1:
                    u = 0.0 if s1 - s0 < 1e-9 else (s - s0) / (s1 - s0)
                    u = u * u * (3.0 - 2.0 * u)
                    ws.append(w0 * (1.0 - u) + w1 * u)
                    break
    return pts, normals, ss, ws


def _tangents(pts):
    out = []
    n = len(pts)
    for i in range(n):
        d = pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]
        out.append(d.normalized() if d.length_squared > 1e-16 else Vector((0.0, 0.0, 1.0)))
    return out


def local_radius(sampler, pts, normals, cap):
    """Half the body thickness under each route point (ray cast straight through)."""
    out = []
    for p, n in zip(pts, normals):
        hit = sampler.ray_cast(p - n * (cap * 1e-3), -n, cap * 2.0)
        out.append(min(cap, (hit.loc - p).length * 0.5) if hit is not None else cap)
    # smooth so the wrap does not jump where the thickness changes
    for _ in range(4):
        out = [out[0]] + [(out[i - 1] + out[i] * 2.0 + out[i + 1]) * 0.25 for i in range(1, len(out) - 1)] + [out[-1]]
    return out


def grow_strands(tree, route, P, scale, push, rng, sampler):
    """Strands twisting along the route. Returns per-node radius list (same indexing as tree).

    Each strand point is rotated around the local centre of the body part
    (route point - normal * R, R = half the local thickness) and snapped back to
    the surface: if the bundle is wider than half the circumference it spirals
    all the way around (limbs, neck), otherwise it weaves inside its width
    (torso)."""
    pts, normals, ss, ws = route
    tans = _tangents(pts)
    total = ss[-1] or 1.0
    count = max(1, P.strand_count)
    radii = [0.0] * len(tree.pos)
    clear = P.clearance * scale
    spread = P.spread * scale
    k_cling = 1.0 + P.cling * 4.0
    turns = P.twist
    R_loc = local_radius(sampler, pts, normals, 0.5 * scale)
    mlen = max(P.meander_length * scale, 1e-6)
    for k in range(count):
        phase = math.tau * k / count + rng.uniform(-0.4, 0.4)
        seed = Vector((rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0, 100)))
        frac = rng.uniform(0.4, 1.0)
        handed = 1.0 if (k % 2 == 0 or not P.counter_twist) else -1.0
        start = rng.uniform(0.0, 0.08) * total * (1.0 - P.strand_sync)
        end = total - rng.uniform(0.0, 0.12) * total * (1.0 - P.strand_sync)
        r_base = P.r_min * scale + (P.r_max - P.r_min) * scale * rng.uniform(0.35, 1.0)
        lift_amp = spread * (rng.random() ** k_cling)
        cur = -1
        for i, (p, n, t, s, w, R) in enumerate(zip(pts, normals, tans, ss, ws, R_loc)):
            if s < start or s > end:
                continue
            ref = n - t * n.dot(t)
            ref = ref.normalized() if ref.length_squared > 1e-10 else n
            theta = phase + handed * math.tau * turns * s / scale
            wob = noise.noise_vector(seed + Vector((s / mlen * 0.7, 0.0, 0.0)))
            span = w / max(R, 1e-6)  # angular half-width of the bundle around the part
            # Thin parts (bundle about as wide as the part): twine all the way round.
            # Wide parts (torso): weave back and forth inside the bundle width.
            b = (span - P.wrap_threshold * 0.6) / max(P.wrap_threshold * 0.8, 1e-6)
            b = max(0.0, min(1.0, b))
            b = b * b * (3.0 - 2.0 * b)
            phi = (span * frac * math.sin(theta)) * (1.0 - b) + theta * b
            phi += P.meander * 0.5 * math.sin(math.tau * s / mlen + phase * 3.0) * min(1.0, span) + 0.2 * wob.x
            centre = p - ref * R
            radial = Matrix.Rotation(phi, 3, t) @ ref
            guess = centre + radial * R
            hit = sampler.nearest(guess)
            lift = lift_amp * max(0.0, 0.5 + 0.5 * wob.y)
            if hit is not None:
                q, nn = push(hit.loc + hit.normal * (clear + lift))
            else:
                q, nn = push(guess + radial * (clear + lift))
            u = (s - start) / max(end - start, 1e-9)
            taper = min(1.0, u / 0.08 + 0.25) * (1.0 - 0.7 * u)
            d = t if cur < 0 else (q - tree.pos[cur])
            if cur >= 0 and d.length < P.step * scale * 0.3:
                continue
            idx = tree.add(q, cur, d.normalized() if d.length_squared > 1e-16 else t, nn, 0, phase)
            tree.plen[idx] = s  # growth animation follows the route from the first waypoint
            radii.append(max(P.r_min * scale, r_base * taper))
            cur = idx
    return radii


def add_shoots(tree, radii, P, scale, push, rng):
    """Short side shoots wandering off the strands, ending in a curl."""
    if P.shoot_density <= 0.0:
        return
    step = P.step * scale
    base = [i for i, k in enumerate(tree.kind) if k == 0 and tree.parent[i] >= 0]
    interval = 1.0 / (P.shoot_density / scale)
    acc = 0.0
    nxt = interval * rng.random()
    for i in base:
        acc += step
        if acc < nxt:
            continue
        nxt = acc + interval * rng.uniform(0.4, 1.6)
        n = tree.normal[i]
        side = tree.dir[i].cross(n)
        side = side.normalized() if side.length_squared > 1e-12 else colonize._rand_unit(rng)
        d = (side * (1 if rng.random() < 0.5 else -1) + n * rng.uniform(0.1, 0.8) + tree.dir[i] * 0.5).normalized()
        length = P.shoot_length * scale * rng.uniform(0.5, 1.5)
        cur, pos = i, tree.pos[i]
        r0 = radii[i] * 0.55
        steps = max(2, int(length * 0.7 / step))
        for j in range(steps):
            d = (d + colonize._rand_unit(rng) * P.wander * 0.5).normalized()
            pos, nn = push(pos + d * step)
            cur = tree.add(pos, cur, d.copy(), nn, 1, tree.phase[cur])
            radii.append(max(P.r_min * scale * 0.7, r0 * (1.0 - 0.6 * j / steps)))
        before = len(tree.pos)
        colonize._curl(tree, cur, d, tree.normal[cur], length * 0.3, step * 0.5, rng.uniform(1.0, 2.5), push, rng, 2)
        radii.extend([P.r_min * scale * 0.6] * (len(tree.pos) - before))


def add_aerial_route(tree, radii, P, scale, push, rng):
    before = len(tree.pos)
    colonize.add_aerial(tree, P, scale, push, rng)
    # radius for the new nodes: thin, tapering
    for i in range(before, len(tree.pos)):
        par = tree.parent[i]
        r = radii[par] * 0.92 if par < len(radii) else P.r_min * scale
        radii.append(max(P.r_min * scale * 0.6, r))


def add_tip_tendrils(tree, radii, P, scale, push, rng):
    before = len(tree.pos)
    colonize.add_tendrils(tree, P, scale, push, rng)
    radii.extend([P.r_min * scale * 0.6] * (len(tree.pos) - before))
