"""Density mode: climbing vines growing thickly around the points.

The density is painted on many points scattered over the surface (vines per
100 cm^2, Yeti-style), blended smoothly between neighbours. There is no hard
range. Vines start where the field is high and climb in random, curling directions, so there is no overall
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


class _FieldBase:
    """Density on the body surface (vines per 100 cm^2), sampled at surface nodes."""

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

    def raw_at(self, p):
        """The painted value (as density) of the nearest point, without the blending."""
        raw = getattr(self, "raw", None)
        if raw is None:
            return self.at(p)
        _co, i, d = self.kd.find(p)
        return raw[i] if d is not None and d <= self.spacing * 1.8 else 0.0

    def dist_at(self, p):
        _co, i, _d = self.kd.find(p)
        return self.dist[i]

    def sample(self, rng):
        i = min(bisect.bisect_left(self.cdf, rng.random() * self.total), len(self.cdf) - 1)
        return self.nodes[i], self.owner[i]

    def count(self, scale):
        """Number of vines: integral of the density (per 100 cm^2) over the surface."""
        return self.total * self.spacing * self.spacing / (0.01 * scale * scale)


class _PNode:
    __slots__ = ("co", "normal")

    def __init__(self, co, normal):
        self.co, self.normal = co, normal


class PointField(_FieldBase):
    """Density from the painted points (Yeti-style). The points already sample the surface, so
    they are the field's nodes; the values are blended over `blend` so neighbouring points merge."""

    def __init__(self, sampler, ps, mw, P, scale):
        rot = mw.to_3x3()
        self.nodes = [_PNode(mw @ c, (rot @ n).normalized()) for c, n in zip(ps.co, ps.nrm)]
        n = len(self.nodes)
        sc = max(mw.to_scale()) or 1.0
        self.spacing = max(ps.spacing() * sc, 1e-6)
        self.owner = [0] * n
        self.dist = [0.0] * n
        self.max = 0.0
        self.total = 0.0
        self.dens = [0.0] * n
        if n < 2:
            return
        self.kd = KDTree(n)
        for i, nd in enumerate(self.nodes):
            self.kd.insert(nd.co, i)
        self.kd.balance()
        blend = max(P.point_blend * scale, self.spacing * 0.5)
        # painted 0..100 -> density. Squared so the colours read clearly: 100 (orange) is the full
        # overall density, 50 (green) a quarter, 25 (blue) only a few vines, 0 (purple) none.
        vals = [(max(0.0, min(v, 100.0)) / 100.0) ** 2 * P.density for v in ps.val]
        self.raw = list(vals)  # the painted values themselves (each vine keeps to its painted level)
        for i, nd in enumerate(self.nodes):
            sw = sv = 0.0
            for _co, j, d in self.kd.find_range(nd.co, blend * 2.0):
                if self.nodes[j].normal.dot(nd.normal) < 0.0:
                    continue  # other side of a thin part
                w = math.exp(-(d / blend) ** 2)
                sw += w
                sv += w * vals[j]
            self.dens[i] = max(0.0, sv / sw) if sw else max(0.0, vals[i])
        self.max = max(self.dens)
        self.cdf, acc = [], 0.0
        for d in self.dens:
            acc += d
            self.cdf.append(acc)
        self.total = acc


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


def _floor(here, home):
    """Lowest density a vine may go on into: a sudden drop (the edge of a stroke) or drifting far
    below the level it started at sends it back. Soft brush gradients stay passable."""
    return max(here * 0.7, home * 0.35)


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
        home = self.field.raw_at(ps)  # the painted level where this vine started: it keeps to that level
        steps = max(3, int(length / step))
        outside = 0
        target = None  # direction it is turning back to after reaching the edge of the paint
        for j in range(steps):
            if j == 6 and parent_vid is not None:
                skip.discard(parent_vid)
            u = j / steps
            nz = noise.noise(seed + Vector((s / self.curl_len, 0.0, 0.0)))
            ang = (curl * 0.25 + nz * P.curl * 1.6) * step / self.curl_len * math.tau * 0.25
            # Stay in the paint. At the edge of a stroke (a sudden drop ahead) or when it has drifted far
            # below the level it started at, it makes a U-turn back instead of following the edge, so the
            # stems spread over the painted area rather than piling up along its border.
            if home > 0.0:
                here = self.field.at(ps)
                if target is None and self.field.at(ps + d * step * 4.0) < _floor(here, home):
                    target = self._turn_back(ps, n, d, _floor(here, home))
                    if target is None:
                        break  # nowhere to go inside the paint
                if target is not None:
                    t = _tangent(target, n)
                    turn = math.atan2(n.dot(d.cross(t)), max(-1.0, min(1.0, d.dot(t))))
                    if abs(turn) < 0.15:
                        target = None
                    ang = max(-0.45, min(0.45, turn))
                if here < home * 0.3:
                    outside += 3 if here < home * 0.05 else 1  # unpainted ground: give up quickly
                else:
                    outside = 0
                if outside > 6:
                    break  # strayed into a much sparser part: end here
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
            # shoots into the air only from well painted spots, so they do not stray from the paint
            dense = home >= self.field.max * 0.5 and self.field.at(ps) >= home * 0.75
            if dense and rng.random() < P.fine_aerial:
                cur = self.aerial(cur, vid, r_tip)
            if cur >= 0 and rng.random() < 0.5:
                self.tendril_spots.append((cur, r_tip, k))

    # ------------------------------------------------------------------
    def _extent(self, p, n, home):
        """Rough size of the painted patch around p: mean distance to its edge in 6 directions."""
        probe = max(self.step * 4.0, self.field.spacing)
        t1 = n.orthogonal().normalized()
        t2 = n.cross(t1)
        total = 0.0
        for k in range(6):
            a = math.tau * k / 6
            d = t1 * math.cos(a) + t2 * math.sin(a)
            dist = 0.0
            while dist < self.mean_len:
                dist += probe
                if self.field.at(p + d * dist) < home * 0.35:
                    break
            total += dist
        return total / 6.0

    def _turn_back(self, p, n, d, floor):
        """A direction back into the paint, preferably away from the edge it just met."""
        probe = self.step * 6.0
        side = n.cross(d)
        good = []
        for k in range(12):
            a = math.tau * k / 12
            v = d * math.cos(a) + side * math.sin(a)
            if self.field.at(p + v * probe) >= floor:
                good.append(v)
        if not good:
            return None
        back = [v for v in good if v.dot(d) < 0.0] or good
        return self.rng.choice(back)

    def aerial(self, cur, vid, r_end):
        """The tip leaves the body and reaches out into the air."""
        P, rng, tree = self.P, self.rng, self.tree
        step = self.step * 1.3
        n = tree.normal[cur]
        d = (tree.dir[cur] + n * rng.uniform(0.6, 1.6) + UP * P.aerial_lift * 0.4).normalized()
        length = self.mean_len * rng.uniform(0.05, 0.15)
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
        count = max(1, count)
        self.queue = []
        for _ in range(count):
            nd, k = field.sample(rng)
            self.use(k)
            hit = self.sampler.nearest(nd.co + colonize._rand_unit(rng) * field.spacing * 0.5)
            if hit is None:
                continue
            length = self.mean_len * rng.lognormvariate(0.0, 0.35)
            home = field.raw_at(hit.loc)
            if home <= 0.0:
                continue  # the soft edge of the paint, on unpainted ground: nothing grows from there
            starts = [hit]
            if home > 0.0:
                # In a small painted patch a long vine would only go round and round: fit its length to
                # the patch, and grow more (shorter) vines there so the amount of stem stays the same.
                ext = self._extent(hit.loc, hit.normal, home)
                fitted = min(length, ext * 4.0)
                # shorter vines also carry fewer and shorter side branches, hence the power
                extra = (length / fitted) ** 1.7 - 1.0
                n_extra = int(extra) + (1 if rng.random() < extra - int(extra) else 0)
                t1 = hit.normal.orthogonal().normalized()
                t2 = hit.normal.cross(t1)
                for _e in range(n_extra):
                    a, rr = rng.uniform(0.0, math.tau), ext * 0.8 * math.sqrt(rng.random())
                    h2 = self.sampler.nearest(hit.loc + (t1 * math.cos(a) + t2 * math.sin(a)) * rr)
                    if h2 is not None and field.raw_at(h2.loc) >= home * 0.35:
                        starts.append(h2)
                length = fitted
            for h in starts:
                d0 = _tangent(colonize._rand_unit(rng), h.normal)
                r0 = self.r_lo + (self.r_hi - self.r_lo) * (rng.random() ** 1.3)
                self.queue.append((h, d0, -1, length, r0, self.clear + r0, k))
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
