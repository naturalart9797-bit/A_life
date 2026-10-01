"""Vines that crawl over the body surface (upper body / 'bodice' part)."""

import math

from mathutils import Quaternion, Vector

from .sampler import random_unit

UP = Vector((0.0, 0.0, 1.0))


class VinePath:
    """A polyline that will be turned into a tube.

    points  : world-space centre line before the radius offset is applied
    normals : outward direction used for the tube frame / surface offset
    weights : per-point bone weight dicts (filled in later)
    sway    : per-point underwater sway weight (filled in later)
    """

    __slots__ = ("points", "normals", "radii", "kind", "depth", "weights", "sway", "hits",
                 "closed", "tparams")

    def __init__(self, kind, depth=0):
        self.points = []
        self.normals = []
        self.radii = []
        self.kind = kind
        self.depth = depth
        self.weights = []
        self.sway = []
        self.hits = []
        self.closed = False
        self.tparams = None


class SpatialHash:
    def __init__(self, cell):
        self.cell = cell
        self.cells = {}

    def _key(self, p):
        c = self.cell
        return (math.floor(p.x / c), math.floor(p.y / c), math.floor(p.z / c))

    def insert(self, p, owner, idx):
        self.cells.setdefault(self._key(p), []).append((p, owner, idx))

    def closer_than(self, p, r, skip=None):
        kx, ky, kz = self._key(p)
        r2 = r * r
        cells = self.cells
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    bucket = cells.get((kx + dx, ky + dy, kz + dz))
                    if not bucket:
                        continue
                    for q, o, i in bucket:
                        if (q - p).length_squared < r2 and not (skip and skip(o, i)):
                            return True
        return False


def _tangential(v, n):
    return v - n * v.dot(n)


class _Grower:
    __slots__ = ("id", "parent", "children", "hit", "dir", "depth", "step", "max_steps",
                 "sign", "path", "alive")


def grow_body_vines(sampler, P, rng, scale, z_lo, z_hi):
    spacing = P.spacing * scale
    step = P.step_length * scale
    hsh = SpatialHash(max(spacing, step) * 1.01)
    grace = int(math.ceil(spacing / step * 2.0)) + 2
    cand_step = math.radians(22.0)

    def in_region(hit):
        z = hit.loc.z
        return z_lo <= z <= z_hi and sampler.mask_at(hit) >= 0.5

    # Candidate seed locations spread over the region.
    samples = []
    tries = 0
    while len(samples) < 2500 and tries < 25000:
        tries += 1
        h = sampler.sample(rng)
        if in_region(h):
            samples.append(h)
    if not samples:
        return []

    paths = []
    next_id = [0]

    def new_grower(hit, direction, depth, parent):
        g = _Grower()
        g.id = next_id[0]
        next_id[0] += 1
        g.parent = parent
        g.children = set()
        g.hit = hit
        g.dir = direction
        g.depth = depth
        g.step = 0
        g.max_steps = max(4, int(P.max_steps * (0.6 ** depth)))
        g.sign = 1.0 if rng.random() < 0.5 + P.spiral_bias * 0.5 else -1.0
        g.path = VinePath("body", depth)
        g.path.points.append(hit.loc.copy())
        g.path.normals.append(hit.normal.copy())
        g.alive = True
        return g

    def wrap_dir(n):
        w = UP.cross(n)
        if w.length_squared < 1e-6:
            return None
        return w.normalized()

    cover_r = spacing * 1.6
    for _pass in range(max(1, P.coverage_passes)):
        uncovered = [s for s in samples if not hsh.closer_than(s.loc, cover_r)]
        if not uncovered:
            break
        rng.shuffle(uncovered)
        seeds = []
        for s in uncovered:
            if len(seeds) >= P.vine_count:
                break
            if any((s.loc - q.loc).length < spacing * 4.0 for q in seeds):
                continue
            seeds.append(s)

        growers = []
        for s in seeds:
            w = wrap_dir(s.normal) or _tangential(random_unit(rng), s.normal).normalized()
            d = _tangential(w + random_unit(rng) * 0.6, s.normal)
            if d.length_squared < 1e-9:
                continue
            g = new_grower(s, d.normalized(), 0, None)
            hsh.insert(s.loc, g.id, 0)
            growers.append(g)

        while growers:
            spawned = []
            for g in growers:
                if not g.alive:
                    continue
                if g.step >= g.max_steps or not _advance(g, sampler, P, rng, hsh, step, spacing,
                                                         grace, cand_step, in_region, wrap_dir):
                    g.alive = False
                    if len(g.path.points) >= 4:
                        paths.append(g.path)
                    continue
                if (g.depth < P.max_depth and g.step > grace and rng.random() < P.branch_chance):
                    ang = math.radians(rng.uniform(35.0, 70.0)) * (1 if rng.random() < 0.5 else -1)
                    d = Quaternion(g.hit.normal, ang) @ g.dir
                    child = new_grower(g.hit, d, g.depth + 1, g.id)
                    g.children.add(child.id)
                    spawned.append(child)
            growers = [g for g in growers if g.alive] + spawned

    return paths


def _advance(g, sampler, P, rng, hsh, step, spacing, grace, cand_step, in_region, wrap_dir):
    n = g.hit.normal
    pos = g.hit.loc
    desired = g.dir * P.persistence
    w = wrap_dir(n)
    if w is not None:
        desired += w * (P.wrap * g.sign)
    vt = _tangential(UP, n)
    if vt.length_squared > 1e-6:
        desired += vt.normalized() * P.vertical_bias
    desired += _tangential(random_unit(rng), n) * P.noise
    desired = _tangential(desired, n)
    if desired.length_squared < 1e-9:
        desired = _tangential(g.dir, n)
        if desired.length_squared < 1e-9:
            return False
    desired.normalize()

    first = 1.0 if rng.random() < 0.5 else -1.0
    angles = [0.0]
    for k in range(1, 4):
        angles += [first * k * cand_step, -first * k * cand_step]

    gid, parent, children, cur = g.id, g.parent, g.children, g.step

    def skip(o, i):
        if o == gid:
            return i > cur - grace
        if o == parent and cur < grace:
            return True
        return o in children and i < grace

    for ang in angles:
        c = Quaternion(n, ang) @ desired if ang else desired
        hit = sampler.nearest(pos + c * step)
        if hit is None:
            continue
        moved = (hit.loc - pos).length
        if moved < step * 0.25 or moved > step * 2.5:
            continue
        if not in_region(hit):
            continue
        if hsh.closer_than(hit.loc, spacing, skip):
            continue
        d = _tangential(hit.loc - pos, hit.normal)
        if d.length_squared < 1e-12:
            continue
        g.dir = d.normalized()
        g.hit = hit
        g.step += 1
        g.path.points.append(hit.loc.copy())
        g.path.normals.append(hit.normal.copy())
        hsh.insert(hit.loc, gid, g.step)
        return True
    return False


def finalize_body_paths(paths, sampler, P, scale):
    """Smooth, re-project to the surface and assign radii."""
    r_base = P.radius * scale
    for path in paths:
        pts = path.points
        for _ in range(2):
            pts = [pts[0]] + [(pts[i - 1] + pts[i] * 2.0 + pts[i + 1]) * 0.25
                              for i in range(1, len(pts) - 1)] + [pts[-1]]
        hits = [sampler.nearest(p) for p in pts]
        path.points = [h.loc for h in hits]
        path.normals = [h.normal for h in hits]
        path.hits = hits
        r0 = r_base * (P.branch_radius ** path.depth)
        count = len(pts)
        radii = []
        for i in range(count):
            t = i / (count - 1)
            r = r0 * max(0.2, 1.0 - P.taper * t)
            tip = count - 1 - i
            if tip < 4:
                r *= 0.35 + 0.65 * tip / 4.0
            radii.append(r)
        path.radii = radii
        off = P.surface_offset * scale
        path.points = [p + n * (off + r) for p, n, r in zip(path.points, path.normals, radii)]
