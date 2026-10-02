"""Auto generation: vines that crawl over a surface inside a painted density region."""

import math

from mathutils import Quaternion, Vector

from .sampler import random_unit


class VinePath:
    """A centre line that will become a tube (world space)."""

    __slots__ = ("points", "normals", "radii", "kind", "depth", "weights", "sway", "hits",
                 "closed", "tparams", "leaf_scale", "attrs")

    def __init__(self, kind="surface", depth=0):
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
        self.leaf_scale = 1.0
        self.attrs = None


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


def weighted_area(sampler, weight_fn, rng, samples=3000):
    """Monte-Carlo estimate of the painted area (area x weight)."""
    acc = 0.0
    for _ in range(samples):
        acc += weight_fn(sampler.sample(rng))
    return sampler.total_area * acc / samples


def grow_vines(sampler, G, rng, scale, weight_fn, up, avoid_points=()):
    """Grow vines over the surface.

    G          : settings (density, length, spacing, wrap ...)
    weight_fn  : hit -> 0..1 paint weight (where vines may grow, and how many seed)
    up         : world-space axis vines wrap around / climb along
    avoid_points: existing guide points the new vines keep away from
    """
    spacing = G.gen_spacing * scale
    step = G.gen_step * scale
    thr = G.gen_threshold
    hsh = SpatialHash(max(spacing, step) * 1.01)
    for p in avoid_points:
        hsh.insert(p, -1, 0)
    grace = int(math.ceil(spacing / step * 2.0)) + 2
    cand_step = math.radians(22.0)

    def in_region(hit):
        return weight_fn(hit) >= thr

    area = weighted_area(sampler, weight_fn, rng)
    n_seeds = int(round(G.gen_density * area / (scale * scale)))
    n_seeds = max(0, min(G.gen_max, n_seeds))
    if n_seeds == 0:
        return []

    # Seeds: rejection sampling by paint weight, kept apart from each other.
    seeds = []
    tries = 0
    min_d = spacing * 2.5
    while len(seeds) < n_seeds and tries < n_seeds * 200:
        tries += 1
        h = sampler.sample(rng)
        w = weight_fn(h)
        if w < thr or rng.random() > w:
            continue
        if hsh.closer_than(h.loc, min_d) or any((h.loc - s.loc).length < min_d for s in seeds):
            continue
        seeds.append(h)

    paths = []
    next_id = [0]
    base_steps = max(4, int(G.gen_length * scale / step))

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
        var = 1.0 + rng.uniform(-1.0, 1.0) * G.gen_length_var
        g.max_steps = max(4, int(base_steps * max(0.1, var) * (0.6 ** depth)))
        g.sign = 1.0 if rng.random() < 0.5 + G.gen_spiral_bias * 0.5 else -1.0
        g.path = VinePath("surface", depth)
        g.path.points.append(hit.loc.copy())
        g.path.normals.append(hit.normal.copy())
        g.alive = True
        return g

    def wrap_dir(n):
        w = up.cross(n)
        if w.length_squared < 1e-6:
            return None
        return w.normalized()

    growers = []
    for s in seeds:
        w = wrap_dir(s.normal) or _tangential(random_unit(rng), s.normal).normalized()
        d = _tangential(w + random_unit(rng) * 0.6, s.normal)
        if d.length_squared < 1e-9:
            continue
        g = new_grower(s, d.normalized(), 0, None)
        hsh.insert(s.loc, g.id, 0)
        growers.append(g)

    ctx = (sampler, G, rng, hsh, step, spacing, grace, cand_step, in_region, wrap_dir, up)
    while growers:
        spawned = []
        for g in growers:
            if not g.alive:
                continue
            if g.step >= g.max_steps or not _advance(g, ctx):
                g.alive = False
                # Vines that got crowded out right away just add clutter.
                min_pts = max(4, int(min(base_steps * 0.3, 0.08 * scale / step) * (0.5 ** g.depth)))
                if len(g.path.points) >= min_pts:
                    paths.append(g.path)
                continue
            if g.depth < G.gen_depth and g.step > grace and rng.random() < G.gen_branch:
                ang = math.radians(rng.uniform(35.0, 70.0)) * (1 if rng.random() < 0.5 else -1)
                d = Quaternion(g.hit.normal, ang) @ g.dir
                child = new_grower(g.hit, d, g.depth + 1, g.id)
                g.children.add(child.id)
                spawned.append(child)
        growers = [g for g in growers if g.alive] + spawned
    return paths


def _advance(g, ctx):
    sampler, G, rng, hsh, step, spacing, grace, cand_step, in_region, wrap_dir, up = ctx
    n = g.hit.normal
    pos = g.hit.loc
    desired = g.dir * G.gen_persistence
    w = wrap_dir(n)
    if w is not None:
        desired += w * (G.gen_wrap * g.sign)
    vt = _tangential(up, n)
    if vt.length_squared > 1e-6:
        desired += vt.normalized() * G.gen_climb
    desired += _tangential(random_unit(rng), n) * G.gen_noise
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


def finalize(paths, sampler, G):
    """Smooth, re-project to the surface and assign relative radii (1.0 = base, branches thinner)."""
    for path in paths:
        pts = path.points
        for _ in range(2):
            pts = [pts[0]] + [(pts[i - 1] + pts[i] * 2.0 + pts[i + 1]) * 0.25
                              for i in range(1, len(pts) - 1)] + [pts[-1]]
        hits = [sampler.nearest(p) for p in pts]
        path.points = [h.loc for h in hits]
        path.normals = [h.normal for h in hits]
        r0 = G.gen_branch_radius ** path.depth
        path.radii = [r0] * len(pts)  # tapering comes from the style
