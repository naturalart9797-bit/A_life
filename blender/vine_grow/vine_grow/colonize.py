"""Vine growth by space colonisation (Runions et al.), kept outside the body.

Attractors fill a thin shell above the surface; branches grow from the origins
toward them one step at a time, branching naturally where attractors pull in
different directions. Every new node is pushed out of the body, so the vines
wind around it instead of passing through. Aerial shoots and spiral tendrils
are added afterwards, and the pipe model gives the branch thickness.
"""

import math

from mathutils import Matrix, Vector
from mathutils.kdtree import KDTree

UP = Vector((0.0, 0.0, 1.0))


def _rand_unit(rng):
    z = rng.uniform(-1.0, 1.0)
    a = rng.uniform(0.0, math.tau)
    r = math.sqrt(max(0.0, 1.0 - z * z))
    return Vector((r * math.cos(a), r * math.sin(a), z))


class Tree:
    """Nodes with parent links (index -1 = root)."""

    def __init__(self):
        self.pos = []
        self.parent = []
        self.dir = []
        self.normal = []
        self.kind = []  # 0 main growth, 1 aerial shoot, 2 tendril
        self.phase = []  # meander phase
        self.plen = []  # path length from the root

    def add(self, p, parent, d, n, kind=0, phase=0.0):
        self.pos.append(p)
        self.parent.append(parent)
        self.dir.append(d)
        self.normal.append(n)
        self.kind.append(kind)
        self.phase.append(phase)
        self.plen.append(0.0 if parent < 0 else self.plen[parent] + (p - self.pos[parent]).length)
        return len(self.pos) - 1

    def children(self):
        ch = [[] for _ in self.pos]
        for i, par in enumerate(self.parent):
            if par >= 0:
                ch[par].append(i)
        return ch


class Pusher:
    """Keeps points at least `clearance` above the body surface."""

    def __init__(self, sampler, clearance):
        self.sampler = sampler
        self.clearance = clearance

    def __call__(self, p):
        hit = self.sampler.nearest(p)
        if hit is None:
            return p, UP
        d = (p - hit.loc).dot(hit.normal)
        if d < self.clearance:
            p = p + hit.normal * (self.clearance - d)
        return p, hit.normal


def make_attractors(anchors, P, scale, rng):
    """Shell of attractors above the surface anchors (mostly close, some far)."""
    H = P.spread * scale
    clear = P.clearance * scale
    k = 1.0 + P.cling * 4.0
    out = []
    for a in anchors:
        h = clear + H * (rng.random() ** k)
        jitter = _rand_unit(rng) * (P.attractor_spacing * scale * 0.4)
        out.append(a.co + a.normal * h + jitter)
    return out


def colonize(tree, attractors, P, scale, push, rng, max_nodes):
    step = P.step * scale
    d_infl = P.influence * scale
    d_kill = max(step * 1.5, P.attractor_spacing * scale * 0.7)
    alive = list(range(len(attractors)))
    inertia = P.inertia
    wander = P.wander
    meander = P.meander
    wavelength = max(P.meander_length * scale, step * 2.0)
    for _ in range(P.max_iterations):
        if not alive or len(tree.pos) >= max_nodes:
            break
        kd = KDTree(len(tree.pos))
        for i, p in enumerate(tree.pos):
            kd.insert(p, i)
        kd.balance()
        pull = {}
        still = []
        for ai in alive:
            a = attractors[ai]
            co, ni, d = kd.find(a)
            if ni is None:
                continue
            if d < d_kill:
                continue  # reached: this attractor is consumed
            still.append(ai)
            if d < d_infl:
                v = (a - co) / d
                acc = pull.get(ni)
                pull[ni] = v if acc is None else acc + v
        alive = still
        if not pull:
            break
        for ni, v in pull.items():
            if v.length_squared < 1e-12:
                v = _rand_unit(rng)
            d = v.normalized() * (1.0 - inertia) + tree.dir[ni] * inertia + _rand_unit(rng) * wander
            if d.length_squared < 1e-12:
                continue
            # Meander: swing the heading side to side around the surface normal -> sinuous vines.
            phase = tree.phase[ni] + rng.uniform(-0.3, 0.3)
            if meander > 0.0:
                ang = meander * math.sin(math.tau * tree.plen[ni] / wavelength + phase)
                d = Matrix.Rotation(ang, 3, tree.normal[ni]) @ d
            p, n = push(tree.pos[ni] + d.normalized() * step)
            q = p - tree.pos[ni]
            if q.length < step * 0.2:
                continue
            _co, _j, dd = kd.find(p)
            if dd is not None and dd < step * 0.35:
                continue  # would just duplicate an existing node
            tree.add(p, ni, q.normalized(), n, 0, phase)
            if len(tree.pos) >= max_nodes:
                break
    return tree


def prune(tree, min_len):
    """Remove short side twigs (tip chains shorter than min_len that hang off a fork)."""
    if min_len <= 0.0:
        return tree
    ch = tree.children()
    dead = set()
    for i in range(len(tree.pos)):
        if ch[i] or tree.parent[i] < 0:
            continue
        seq = [i]
        cur = i
        while tree.parent[cur] >= 0 and len(ch[tree.parent[cur]]) == 1:
            cur = tree.parent[cur]
            seq.append(cur)
        fork = tree.parent[cur]
        if fork < 0:
            continue  # the whole branch from the root: keep
        if tree.plen[i] - tree.plen[fork] < min_len:
            dead.update(seq)
    if not dead:
        return tree
    keep = [i for i in range(len(tree.pos)) if i not in dead]
    remap = {old: new for new, old in enumerate(keep)}
    out = Tree()
    for old in keep:
        par = tree.parent[old]
        out.add(tree.pos[old], remap.get(par, -1) if par >= 0 else -1, tree.dir[old], tree.normal[old],
                tree.kind[old], tree.phase[old])
    return out


def _curl(tree, start, d, n, length, step, coils, push, rng, kind):
    """Spiral tendril continuing from node `start`."""
    steps = max(3, int(length / step))
    axis = d.cross(n)
    if axis.length_squared < 1e-8:
        axis = d.cross(UP) if abs(d.dot(UP)) < 0.9 else d.cross(Vector((1.0, 0.0, 0.0)))
    axis.normalize()
    side = 1.0 if rng.random() < 0.5 else -1.0
    cur, pos = start, tree.pos[start]
    for j in range(steps):
        u = j / steps
        ang = coils * math.tau / steps * (0.2 + 1.8 * u)  # tighter toward the tip
        d = Matrix.Rotation(ang * side, 3, axis) @ d
        axis = (Matrix.Rotation(0.15 * side, 3, d) @ axis).normalized()  # 3D corkscrew
        pos, nn = push(pos + d * step)
        cur = tree.add(pos, cur, d.copy(), nn, kind, tree.phase[cur])
    return cur


def add_aerial(tree, P, scale, push, rng):
    """Shoots that lift off the body into the air and end in a curl."""
    if P.aerial_count <= 0 or len(tree.pos) < 2:
        return
    step = P.step * scale
    cand = [i for i, k in enumerate(tree.kind) if k == 0 and tree.parent[i] >= 0]
    rng.shuffle(cand)
    for i in cand[:P.aerial_count]:
        n = tree.normal[i]
        d = (n + UP * P.aerial_lift + _rand_unit(rng) * 0.4).normalized()
        length = P.aerial_length * scale * rng.uniform(0.5, 1.5)
        steps = max(3, int(length * 0.7 / step))
        cur, pos = i, tree.pos[i]
        for _ in range(steps):
            d = (d + _rand_unit(rng) * P.wander * 0.6 + UP * (P.aerial_lift * 0.06)).normalized()
            pos, nn = push(pos + d * step)
            cur = tree.add(pos, cur, d.copy(), nn, 1, tree.phase[cur])
        _curl(tree, cur, d, tree.normal[cur], length * 0.3, step * 0.6, rng.uniform(1.0, 2.5), push, rng, 2)


def add_tendrils(tree, P, scale, push, rng):
    if P.tendril_chance <= 0.0:
        return
    step = P.step * scale
    ch = tree.children()
    tips = [i for i in range(len(tree.pos)) if not ch[i] and tree.kind[i] == 0]
    for i in tips:
        if rng.random() > P.tendril_chance:
            continue
        length = P.tendril_length * scale * rng.uniform(0.6, 1.4)
        _curl(tree, i, tree.dir[i], tree.normal[i], length, step * 0.5, rng.uniform(1.5, 3.5), push, rng, 2)


def pipe_radii(tree, r_min, r_max, exponent=2.5):
    """da Vinci / pipe model: r_parent^n = sum(r_child^n); scaled into [r_min, r_max]."""
    n = len(tree.pos)
    raw = [0.0] * n
    ch = tree.children()
    for i in range(n - 1, -1, -1):  # children always come after their parent
        if not ch[i]:
            raw[i] = 1.0
        else:
            raw[i] = sum(raw[c] ** exponent for c in ch[i]) ** (1.0 / exponent)
    top = max(raw) if raw else 1.0
    out = []
    for i in range(n):
        t = (raw[i] - 1.0) / max(top - 1.0, 1e-9)
        r = r_min + (r_max - r_min) * t
        if tree.kind[i] == 2:
            r = r_min * 0.6
        out.append(r)
    return out


def path_lengths(tree):
    return list(tree.plen)


def chains(tree):
    """Split the tree into chains between branch points (each starts at its parent node)."""
    ch = tree.children()
    out = []
    starts = [i for i, par in enumerate(tree.parent) if par < 0]
    stack = [(s, None) for s in starts]
    while stack:
        node, _ = stack.pop()
        for c in ch[node]:
            seq = [node, c]
            cur = c
            while len(ch[cur]) == 1:
                cur = ch[cur][0]
                seq.append(cur)
            out.append(seq)
            if ch[cur]:
                stack.append((cur, None))
    return out
