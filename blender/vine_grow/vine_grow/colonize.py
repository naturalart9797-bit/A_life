"""Shared vine structures: the node tree, the push-out-of-the-body helper,
spiral tendrils and splitting the tree into chains for meshing."""

import math

from mathutils import Matrix, Vector

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
