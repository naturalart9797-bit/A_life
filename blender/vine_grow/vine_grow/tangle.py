"""Density mode: climbing vines growing thickly around the points.

Each point carries its own density (vines per 100 cm^2) that fades smoothly
along the body surface, so dense and sparse parts can be set point by point,
with no hard range. Vines start where the field is high and climb in random, curling directions, so there is no overall
flow. Each vine is one continuous stem, thick at its base and thin at its tip,
with internodes (slight swellings), side branches and tendrils:

* the stem arches away from the body and comes back, and crosses over the
  stems it meets, so the tangle has real depth;
* some tips leave the body and reach out into the air;
* tendrils coil around a neighbouring stem when one is in reach, otherwise
  they curl up on their own.
Each point sets its own density; vines lean back toward the dense parts
but are free to wander anywhere.
"""

import bisect
import math

from mathutils import Matrix, Vector, noise
from mathutils.kdtree import KDTree

from . import colonize, surface

UP = Vector((0.0, 0.0, 1.0))


class Field:
    """Vine density on the body surface (vines per 100 cm^2).

    Every point adds its own density, fading smoothly with the distance along
    the body (gaussian, width = falloff). Nothing is cut off: the vines may
    wander anywhere, the field only decides where they are thick or sparse."""

    def __init__(self, sampler, pts, P, scale, rng):
        """pts: (surface hit, density, range) per point. The density fades to ~2% at the range."""
        reaches = [max(r, 0.005 * scale) for _, _, r in pts]
        spacing = max(min(reaches) / 16.0, max(reaches) / 40.0, 0.002 * scale)
        locs = [h.loc for h, _, _ in pts]
        self.nodes = surface.scatter_nodes(sampler, locs, max(reaches) * 1.25, spacing, rng)
        self.spacing = spacing
        n = len(self.nodes)
        self.dens = [0.0] * n
        self.owner = [0] * n  # point with the strongest contribution: its settings are used there
        self.dist = [math.inf] * n  # distance to the nearest point: drives the growth animation
        self.max = 0.0
        self.total = 0.0
        if n < 4:
            return
        edges = surface.connect(sampler, self.nodes, spacing)
        best = [0.0] * n
        for k, ((hit, value, _r), reach) in enumerate(zip(pts, reaches)):
            if value <= 0.0:
                continue
            sigma = reach * 0.5
            s = surface.nearest_node(self.nodes, hit.loc)
            for i, d in enumerate(surface.geodesic(self.nodes, edges, [s])):
                if d < reach * 1.25:
                    c = value * math.exp(-(d / sigma) ** 2)
                    self.dens[i] += c
                    if c > best[i]:
                        best[i], self.owner[i] = c, k
                    self.dist[i] = min(self.dist[i], d)
        if P.contrast != 1.0:
            # sharpen dense vs sparse while keeping each point's own value at its centre
            peaks = [v for _, v, _ in pts]
            self.dens = [(peaks[k] * (x / peaks[k]) ** P.contrast) if x > 0.0 and peaks[k] > 0.0 else x
                         for x, k in zip(self.dens, self.owner)]
        self.max = max(self.dens)
        self.kd = KDTree(n)
        for i, nd in enumerate(self.nodes):
            self.kd.insert(nd.co, i)
        self.kd.balance()
        self.cdf, acc = [], 0.0
        for d in self.dens:
            acc += d
            self.cdf.append(acc)
        self.total = acc

    def ok(self):
        return len(self.nodes) >= 4 and self.total > 0.0

    def at(self, p):
        found = self.kd.find_n(p, 3)
        if not found or found[0][2] > self.spacing * 1.8:
            return 0.0  # off the scattered part of the surface (e.g. a neighbouring limb)
        sw = sv = 0.0
        for _co, i, d in found:
            w = 1.0 / (d + self.spacing * 0.3)
            sw += w
            sv += w * self.dens[i]
        return sv / sw if sw else 0.0

    def dist_at(self, p):
        _co, i, _d = self.kd.find(p)
        return self.dist[i]

    def sample(self, rng):
        i = min(bisect.bisect_left(self.cdf, rng.random() * self.total), len(self.cdf) - 1)
        return self.nodes[i], self.owner[i]

    def count(self, scale):
        """Number of vines: integral of the density (per 100 cm^2) over the surface."""
        return self.total * self.spacing * self.spacing / (0.01 * scale * scale)


class Occupancy:
    """Spatial hash of the stem nodes laid so far: (position, height above skin, radius, vine id, node)."""

    def __init__(self, cell):
        self.cell = cell
        self.grid = {}

    def _key(self, p):
        c = self.cell
        return (math.floor(p.x / c), math.floor(p.y / c), math.floor(p.z / c))

    def add(self, p, h, r, vid, idx):
        self.grid.setdefault(self._key(p), []).append((p, h, r, vid, idx))

    def _cells(self, p, rad):
        m = max(1, int(math.ceil(rad / self.cell)))
        kx, ky, kz = self._key(p)
        for dx in range(-m, m + 1):
            for dy in range(-m, m + 1):
                for dz in range(-m, m + 1):
                    yield from self.grid.get((kx + dx, ky + dy, kz + dz), ())

    def near(self, p, rad, skip):
        out = []
        for item in self._cells(p, rad):
            if item[3] in skip:
                continue
            d = (item[0] - p).length
            if d < rad + item[2]:
                out.append((d, item))
        return out

    def nearest(self, p, rad, skip):
        best, bd = None, rad
        for item in self._cells(p, rad):
            if item[3] in skip:
                continue
            d = (item[0] - p).length
            if d < bd:
                best, bd = item, d
        return best


def _tangent(d, n):
    t = d - n * d.dot(n)
    if t.length_squared < 1e-12:
        t = n.orthogonal()
    return t.normalized()


def _smooth01(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


class _VP:
    """Settings of one point, converted to lengths for this target."""

    def __init__(self, P, scale):
        self.P = P
        self.step = P.fine_step * scale
        self.r_lo, self.r_hi = P.fine_r_min * scale, max(P.fine_r_max, P.fine_r_min) * scale
        self.clear = P.clearance * scale
        self.spread = P.spread * scale
        self.k_cling = 1.0 + P.cling * 4.0
        self.curl_len = max(P.curl_length * scale, self.step * 2.0)
        self.mean_len = P.tangle_length * scale
        self.internode = max(P.internode * scale, self.step * 2.0)


class Grower:
    def __init__(self, tree, sampler, field, params, scale, push, rng):
        self.tree, self.sampler, self.field = tree, sampler, field
        self.scale, self.push, self.rng = scale, push, rng
        self.vps = [_VP(p, scale) for p in params]
        self.occ = Occupancy(max(max(v.r_hi * 4.0 + v.step, v.step * 1.5) for v in self.vps))
        self.radii = []
        self.owner = {}  # node -> point index (nodes added by helpers inherit from their parent)
        self.tendril_spots = []  # (node, radius, point)
        self.vid = 0
        self.use(0)

    def use(self, k):
        v = self.vps[k]
        self.k = k
        self.P, self.step, self.r_lo, self.r_hi = v.P, v.step, v.r_lo, v.r_hi
        self.clear, self.spread, self.k_cling = v.clear, v.spread, v.k_cling
        self.curl_len, self.mean_len, self.internode = v.curl_len, v.mean_len, v.internode

    def add(self, p, parent, d, n, kind, vid, r):
        idx = self.tree.add(p, parent, d, n, kind, vid)
        self.radii.append(r)
        self.owner[idx] = self.k
        return idx

    # ------------------------------------------------------------------
    def vine(self, hit, d, parent, length, r_start, h0, k):
        self.use(k)
        P, rng, tree, step = self.P, self.rng, self.tree, self.step
        self.vid += 1
        vid = self.vid
        skip = {vid}
        parent_vid = tree.phase[parent] if parent >= 0 else None
        if parent_vid is not None:
            skip.add(parent_vid)
        r_tip = max(self.r_lo * 0.7, r_start * 0.3)
        arch_amp = self.spread * (rng.random() ** self.k_cling)
        arch_len = self.curl_len * rng.uniform(1.5, 4.0)
        seed = Vector((rng.uniform(0, 500), rng.uniform(0, 500), rng.uniform(0, 500)))
        curl = P.curl * rng.uniform(0.5, 1.5) * (1 if rng.random() < 0.5 else -1)
        ps, n = hit.loc, hit.normal
        cur = parent
        s = 0.0
        over = 0.0
        height = h0
        next_node = self.internode * rng.uniform(0.3, 1.0)
        swell = 0
        steps = max(3, int(length / step))
        for j in range(steps):
            if j == 6 and parent_vid is not None:
                skip.discard(parent_vid)
            u = j / steps
            dens = self.field.at(ps)
            nz = noise.noise(seed + Vector((s / self.curl_len, 0.0, 0.0)))
            ang = (curl * 0.25 + nz * P.curl * 1.6) * step / self.curl_len * math.tau * 0.25
            side = n.cross(d)
            if side.length_squared > 1e-12:
                side.normalize()
                probe = step * 4.0
                # leaning back toward the dense parts (keeps sparse areas sparse)
                fm = self.field.max or 1.0
                edge = max(0.0, 1.0 - dens / fm)
                if P.containment > 0.0 and edge > 0.0 and self.field.at(ps + d * probe) < dens:
                    dl = self.field.at(ps + side * probe)
                    dr = self.field.at(ps - side * probe)
                    ang += P.containment * edge * (dl - dr) / max(dens, fm * 0.05) * 0.8
            d = Matrix.Rotation(ang, 3, n) @ d
            want = ps + d * step
            nh = self.sampler.nearest(want)
            if nh is None or (nh.loc - want).length > step * 2.0:
                break  # would jump across a gap onto another body part
            ps, n = nh.loc, nh.normal
            d = _tangent(d, n)
            s += step
            r = r_tip + (r_start - r_tip) * (1.0 - u) ** 0.9
            # arches: the stem lifts away from the body and comes back
            a = noise.noise(seed + Vector((0.0, s / arch_len, 7.0)))
            lift = arch_amp * _smooth01(a * 1.8 + 0.1) * min(1.0, s / (arch_len * 0.5))
            base = self.clear + r + lift
            q = ps + n * (base + over)
            need = 0.0
            for _dist, item in self.occ.near(q, r * 1.6 + step * 0.3, skip):
                need = max(need, item[1] + item[2] + r * 1.1 - base)  # climb over that stem
            over = max(need, over * 0.8)
            height = base + over
            q, nn = self.push(ps + n * height)
            # internodes: slight swelling, side branches and tendrils come out here
            at_node = s >= next_node
            if at_node:
                next_node = s + self.internode * rng.uniform(0.75, 1.25)
                swell = 2
            rr = r * (1.22 if swell == 2 else 1.08 if swell == 1 else 1.0)
            swell = max(0, swell - 1)
            tw = (q - tree.pos[cur]) if cur >= 0 else d
            idx = self.add(q, cur, tw.normalized() if tw.length_squared > 1e-16 else d, nn, 0, vid, rr)
            if cur < 0:
                tree.plen[idx] = self.field.dist_at(ps)
            self.occ.add(q, height, r, vid, idx)
            cur = idx
            if at_node and j > 2:
                if rng.random() < P.fine_branch:
                    bd = Matrix.Rotation(rng.uniform(0.5, 1.1) * (1 if rng.random() < 0.5 else -1), 3, n) @ d
                    rest = (steps - j) * step
                    self.queue.append((nh, _tangent(bd, n), idx, max(rest * rng.uniform(0.4, 0.9), step * 6),
                                       r * 0.75, height, k))
                if rng.random() < P.tendril_chance * 0.4:
                    self.tendril_spots.append((idx, r, k))
        if cur >= 0 and tree.parent[cur] >= 0:
            if rng.random() < P.fine_aerial:
                cur = self.aerial(cur, vid, r_tip)
            if cur >= 0 and rng.random() < 0.5:
                self.tendril_spots.append((cur, r_tip, k))

    # ------------------------------------------------------------------
    def aerial(self, cur, vid, r_end):
        """The tip leaves the body and reaches out into the air."""
        P, rng, tree = self.P, self.rng, self.tree
        step = self.step * 1.3
        n = tree.normal[cur]
        d = (tree.dir[cur] + n * rng.uniform(0.6, 1.6) + UP * P.aerial_lift * 0.4).normalized()
        length = self.mean_len * rng.uniform(0.08, 0.22)
        steps = max(3, int(length / step))
        seed = Vector((rng.uniform(0, 500), rng.uniform(0, 500), rng.uniform(0, 500)))
        r0 = self.radii[cur]
        pos = tree.pos[cur]
        for j in range(steps):
            w = noise.noise_vector(seed + Vector((j * step / self.curl_len * 0.5, 0.0, 0.0)))
            d = (d + w * (0.15 + P.curl * 0.2) + UP * (P.aerial_lift * 0.03)).normalized()
            pos, nn = self.push(pos + d * step)
            u = (j + 1) / steps
            cur = self.add(pos, cur, d.copy(), nn, 1, vid, max(r_end, r0 * (1.0 - u) + r_end * u))
        # an aerial tip always ends in a curl
        before = len(tree.pos)
        colonize._curl(tree, cur, d, tree.normal[cur], self.curl_len * rng.uniform(0.4, 0.8), self.step * 0.5,
                       rng.uniform(1.5, 3.0), self.push, rng, 2)
        self.radii.extend([max(self.r_lo * 0.45, r_end * 0.8)] * (len(tree.pos) - before))
        return -1

    # ------------------------------------------------------------------
    def tendrils(self):
        P, rng, tree = self.P, self.rng, self.tree
        for idx, r, k in self.tendril_spots:
            self.use(k)
            P = self.P
            rt = max(self.r_lo * 0.45, r * 0.35)
            reach = self.curl_len * 0.8
            tgt = self.occ.nearest(tree.pos[idx], reach, {tree.phase[idx]})
            if tgt is not None and tgt[4] < len(tree.pos):
                self.coil_around(idx, tgt, rt)
            else:
                before = len(tree.pos)
                colonize._curl(tree, idx, tree.dir[idx], tree.normal[idx], self.curl_len * rng.uniform(0.4, 0.8),
                               self.step * 0.4, rng.uniform(1.5, 3.0), self.push, rng, 2)
                self.radii.extend([rt] * (len(tree.pos) - before))

    def coil_around(self, idx, tgt, rt):
        """Tendril reaches a neighbouring stem and winds around it a few times."""
        rng, tree = self.rng, self.tree
        c0, r_o = tgt[0], tgt[2]
        axis = tree.dir[tgt[4]]
        R = r_o + rt * 1.3
        start = tree.pos[idx]
        e1 = start - c0
        e1 = e1 - axis * e1.dot(axis)
        e1 = e1.normalized() if e1.length_squared > 1e-14 else axis.orthogonal().normalized()
        e2 = axis.cross(e1)
        vid = tree.phase[idx]
        cur = idx
        # reach over to the stem
        grab = c0 + e1 * R
        m = max(1, int((grab - start).length / (self.step * 0.5)))
        mid = (start + grab) * 0.5 + tree.normal[idx] * (start - grab).length * 0.25
        for k in range(1, m + 1):
            w = k / m
            p = (start * (1 - w) + mid * w).lerp(mid * (1 - w) + grab * w, w)  # quadratic bezier
            p, nn = self.push(p)
            cur = self.add(p, cur, (p - tree.pos[cur]).normalized(), nn, 2, vid, rt)
        # helix around the stem, tightening and thinning toward the end
        turns = rng.uniform(1.5, 3.5)
        side = 1.0 if rng.random() < 0.5 else -1.0
        pitch = rt * 5.0 * (1 if rng.random() < 0.5 else -1)
        nseg = max(8, int(turns * 10))
        for k in range(1, nseg + 1):
            th = turns * math.tau * k / nseg
            p = c0 + axis * (pitch * th / math.tau) + (e1 * math.cos(th) + e2 * math.sin(th) * side) * R
            tw = p - tree.pos[cur]
            p, nn = self.push(p)
            cur = self.add(p, cur, tw.normalized() if tw.length_squared > 1e-16 else axis, nn, 2, vid,
                           rt * (1.0 - 0.5 * k / nseg))

    # ------------------------------------------------------------------
    def run(self):
        P, rng, field = self.P, self.rng, self.field
        count = int(round(field.count(self.scale)))
        count = max(1, min(count, 5000))
        self.queue = []
        for _ in range(count):
            nd, k = field.sample(rng)
            self.use(k)
            hit = self.sampler.nearest(nd.co + colonize._rand_unit(rng) * field.spacing * 0.5)
            if hit is None:
                continue
            d0 = _tangent(colonize._rand_unit(rng), hit.normal)
            r0 = self.r_lo + (self.r_hi - self.r_lo) * (rng.random() ** 1.3)
            self.queue.append((hit, d0, -1, self.mean_len * rng.lognormvariate(0.0, 0.35), r0,
                               self.clear + r0, k))
        while self.queue and len(self.tree.pos) < P.max_nodes:
            self.vine(*self.queue.pop(0))
        self.tendrils()
        own = []
        for i, par in enumerate(self.tree.parent):
            own.append(self.owner.get(i, own[par] if par >= 0 else 0))
        return self.radii, own


def grow(tree, sampler, field, params, scale, push, rng):
    """Fill `tree` with the vines (params: settings per point). Returns (per-node radii, per-node point)."""
    return Grower(tree, sampler, field, params, scale, push, rng).run()
