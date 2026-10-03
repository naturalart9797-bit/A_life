"""Density mode: a fine, tangled mat of vines concentrated around the points.

The points only say *where* the vines should be dense (a density field that
falls off along the body surface within each point's range). Many thin vines
start at random places drawn from that field and wander in random curling
directions, so there is no overall flow. When a vine meets another one it
either climbs over it (layered, felt-like tangle) or fuses into it, which
gives the net-like, slime-mould look. They turn back where the density drops,
so the mat stays inside the ranges.
"""

import bisect
import math

from mathutils import Matrix, Vector, noise
from mathutils.kdtree import KDTree

from . import colonize, surface


class Field:
    """Density on the body surface: max over points of exp(-k (geodesic d / range)^2)."""

    def __init__(self, sampler, opts, P, scale, rng):
        reaches = [r for _, r in opts]
        spacing = max(min(reaches) / 10.0, max(reaches) / 28.0, 0.002 * scale)
        locs = [h.loc for h, _ in opts]
        self.nodes = surface.scatter_nodes(sampler, locs, max(reaches), spacing, rng)
        self.spacing = spacing
        n = len(self.nodes)
        self.dens = [0.0] * n
        self.dist = [math.inf] * n  # distance (relative to range) to the nearest point: drives the growth animation
        if n < 4:
            return
        edges = surface.connect(sampler, self.nodes, spacing)
        k = 1.0 + P.concentration * 5.0
        for hit, reach in opts:
            s = surface.nearest_node(self.nodes, hit.loc)
            for i, d in enumerate(surface.geodesic(self.nodes, edges, [s])):
                u = d / max(reach, 1e-9)
                if u <= 1.0:
                    self.dens[i] = max(self.dens[i], math.exp(-k * u * u) - math.exp(-k) * u * u)
                    self.dist[i] = min(self.dist[i], d)
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
        return self.nodes[i]

    def area(self):
        """Integral of the density over the surface (m^2)."""
        return self.total * self.spacing * self.spacing


class Occupancy:
    """Spatial hash of the vine nodes laid so far: (position, height above skin, radius, strand)."""

    def __init__(self, cell):
        self.cell = cell
        self.grid = {}

    def _key(self, p):
        c = self.cell
        return (math.floor(p.x / c), math.floor(p.y / c), math.floor(p.z / c))

    def add(self, p, h, r, sid, idx):
        self.grid.setdefault(self._key(p), []).append((p, h, r, sid, idx))

    def near(self, p, rad, skip):
        kx, ky, kz = self._key(p)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for item in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                        if item[3] in skip:
                            continue
                        d = (item[0] - p).length
                        if d < rad + item[2]:
                            out.append((d, item))
        return out


    def nearest_ahead(self, p, d, rad, skip):
        """Closest node of another vine within rad, roughly in front of heading d."""
        c = self.cell
        m = int(math.ceil(rad / c))
        kx, ky, kz = self._key(p)
        best, bd = None, rad
        for dx in range(-m, m + 1):
            for dy in range(-m, m + 1):
                for dz in range(-m, m + 1):
                    for item in self.grid.get((kx + dx, ky + dy, kz + dz), ()):
                        if item[3] in skip:
                            continue
                        v = item[0] - p
                        dist = v.length
                        if dist < bd and v.dot(d) > 0.3 * dist:
                            best, bd = item, dist
        return best


def _tangent(d, n):
    t = d - n * d.dot(n)
    if t.length_squared < 1e-12:
        t = n.orthogonal()
    return t.normalized()


def grow(tree, sampler, field, P, scale, push, rng):
    """Fill `tree` with the tangle. Returns (radii, joins) - joins are node indices where vines fused."""
    step = P.fine_step * scale
    r_lo, r_hi = P.fine_r_min * scale, P.fine_r_max * scale
    clear = P.clearance * scale
    spread = P.spread * scale
    k_cling = 1.0 + P.cling * 4.0
    curl_len = max(P.curl_length * scale, step * 2.0)
    mean_len = P.tangle_length * scale
    count = int(round(P.tangle_density * field.area() / (0.01 * scale * scale)))
    count = max(1, min(count, 20000))
    occ = Occupancy(max(r_hi * 4.0 + step, step * 1.5))
    radii, joins = [], []
    queue = []
    for _ in range(count):
        nd = field.sample(rng)
        jitter = colonize._rand_unit(rng) * field.spacing * 0.5
        hit = sampler.nearest(nd.co + jitter)
        if hit is None:
            continue
        d0 = _tangent(colonize._rand_unit(rng), hit.normal)
        queue.append((hit, d0, -1, mean_len * rng.lognormvariate(0.0, 0.45), None, 0.0))
    sid = 0
    while queue and len(tree.pos) < P.max_nodes:
        hit, d, parent, length, r_parent, h0 = queue.pop(0)
        sid += 1
        skip = {sid}
        r_base = r_lo + (r_hi - r_lo) * (rng.random() ** 1.2)
        if r_parent is not None:
            r_base = min(r_base, r_parent * 0.8)
        lift_amp = spread * (rng.random() ** k_cling)
        seed = Vector((rng.uniform(0, 500), rng.uniform(0, 500), rng.uniform(0, 500)))
        curl = P.curl * rng.uniform(0.5, 1.5) * (1 if rng.random() < 0.5 else -1)
        ps, n = hit.loc, hit.normal
        height = h0 if parent >= 0 else clear + r_base
        cur = parent
        s = 0.0
        over = 0.0  # extra height while climbing over another vine
        parent_sid = None
        if parent >= 0:
            parent_sid = tree.phase[parent]
            skip.add(parent_sid)
        steps = max(3, int(length / step))
        for j in range(steps):
            if j == 4 and parent_sid is not None:
                skip.discard(parent_sid)  # it may cross its own parent further on
            u = j / steps
            dens = field.at(ps)
            if dens < 0.01 and j > 2:
                break
            # wander: curvature from smooth noise along the length -> curls, loops, S-bends
            nz = noise.noise(seed + Vector((s / curl_len, 0.0, 0.0)))
            ang = (curl * 0.25 + nz * P.curl * 1.6) * step / curl_len * math.tau * 0.25
            # steer back toward the dense part when the density drops
            side = n.cross(d)
            if side.length_squared > 1e-12:
                side.normalize()
                probe = step * 4.0
                dl = field.at(ps + side * probe)
                dr = field.at(ps - side * probe)
                ahead = field.at(ps + d * probe)
                edge = max(0.0, 1.0 - dens / 0.3)  # only near the rim of the range
                if ahead < dens and edge > 0.0:
                    ang += P.containment * edge * (dl - dr) / max(dens, 0.05) * 0.8
            d = Matrix.Rotation(ang, 3, n) @ d
            want = ps + d * step
            nh = sampler.nearest(want)
            if nh is None or (nh.loc - want).length > step * 2.0:
                break  # would jump across a gap onto another body part
            ps, n = nh.loc, nh.normal
            d = _tangent(d, n)
            s += step
            # height: own float + climbing over vines it crosses
            base = clear + r_base + lift_amp * max(0.0, noise.noise(seed + Vector((0.0, s / (curl_len * 3.0), 0.0))) + 0.15)
            q = ps + n * (base + over)
            hits = occ.near(q, r_base * 1.6 + step * 0.3, skip)
            joined = None
            need = 0.0
            for dist, item in hits:
                if j > 3 and rng.random() < P.fuse:
                    joined = item
                    break
                need = max(need, item[1] + item[2] + r_base * 1.1 - base)
            if joined is not None:
                p_end = joined[0]
                tw = p_end - tree.pos[cur] if cur >= 0 else d
                idx = tree.add(p_end.copy(), cur, tw.normalized() if tw.length_squared > 1e-16 else d, n, 0, sid)
                radii.append(max(r_lo * 0.7, r_base * 0.7))
                joins.append(idx)
                cur = idx
                break
            over = max(need, over * 0.75)
            height = base + over
            q, nn = push(ps + n * height)
            taper = max(0.5, min(1.0, (u + 0.1) / 0.2, (1.0 - u) / 0.2 + 0.4))
            r = max(r_lo * 0.7, r_base * taper * (0.75 + 0.25 * min(1.0, dens * 1.5)))
            if cur >= 0:
                tw = q - tree.pos[cur]
                tw = tw.normalized() if tw.length_squared > 1e-16 else d
            else:
                tw = d
            idx = tree.add(q, cur, tw, nn, 0, sid)
            if cur < 0:
                tree.plen[idx] = field.dist_at(ps)
            radii.append(r)
            occ.add(q, height, r, sid, idx)
            cur = idx
            # branch now and then
            if j > 2 and rng.random() < P.fine_branch * step / max(mean_len, 1e-9) * 4.0:
                bd = Matrix.Rotation(rng.uniform(0.4, 1.1) * (1 if rng.random() < 0.5 else -1), 3, n) @ d
                queue.append((nh, _tangent(bd, n), idx, length * rng.uniform(0.3, 0.7), r, height))
        # Most free ends reach over to a nearby vine and fuse with it -> a connected net.
        if (cur >= 0 and cur not in joins and tree.parent[cur] >= 0
                and rng.random() < min(1.0, P.fuse * 2.0)):
            tgt = occ.nearest_ahead(tree.pos[cur], tree.dir[cur], curl_len * 1.2, skip | {sid})
            if tgt is not None:
                a, ha = tree.pos[cur], height
                m = max(1, int((tgt[0] - a).length / step))
                for k in range(1, m + 1):
                    w = k / m
                    if k == m:
                        q = tgt[0].copy()
                        nn = tree.normal[tgt[4]]
                    else:
                        g = sampler.nearest(a.lerp(tgt[0], w))
                        if g is None:
                            break
                        q, nn = push(g.loc + g.normal * (ha * (1.0 - w) + tgt[1] * w))
                    tw = q - tree.pos[cur]
                    cur = tree.add(q, cur, tw.normalized() if tw.length_squared > 1e-16 else d, nn, 0, sid)
                    radii.append(max(r_lo * 0.7, radii[tree.parent[cur]] * 0.97))
                    occ.add(q, ha, radii[-1], sid, cur)
                else:
                    joins.append(cur)
        # curl the free tip into a tiny tendril
        if cur >= 0 and tree.parent[cur] >= 0 and cur not in joins and rng.random() < P.tendril_chance * 0.4:
            before = len(tree.pos)
            colonize._curl(tree, cur, tree.dir[cur], tree.normal[cur], mean_len * 0.12, step * 0.5,
                           rng.uniform(1.2, 2.8), push, rng, 2)
            radii.extend([r_lo * 0.6] * (len(tree.pos) - before))
    return radii, joins
