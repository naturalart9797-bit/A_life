"""Leaves along the vines.

Heart-shaped blades (a blend of an oval and a cardioid, so the lobes at the
base can be shallow or deep) on short petioles. They sit alternately left and
right along the stems, face away from the body, cup, fold along the midrib
and droop at the tip. Leaves on thin stems (toward the tips) are smaller and
younger in colour. Every vertex is pushed out of the body.
"""

import math

from mathutils import Matrix, Vector

from . import colonize
from .mesh import tube

N_ANG = 18  # outline segments
N_RING = 4  # rings from the base to the margin


def _outline(phi, P):
    """Distance from the petiole attachment to the margin at angle phi (0 = toward the tip), for length 1."""
    c = math.cos(phi)
    oval = max(0.0, c)
    cardioid = 0.5 * (1.0 + c)
    r = oval * (1.0 - P.leaf_lobe) + cardioid * P.leaf_lobe
    r *= 1.0 + P.leaf_point * 0.35 * math.exp(-(phi / 0.3) ** 2)  # pointed tip
    return r


def _blade(b, base, A, S, N, L, P, push, w, dist, col, rng):
    """Blade with its base at `base`, midrib along A, face normal N."""
    width = P.leaf_width
    cup = P.leaf_cup
    fold = P.leaf_fold
    droop = P.leaf_droop
    wob = rng.uniform(-0.15, 0.15)

    def place(x, y):
        # x: along the midrib, y: to the side (both in units of L)
        z = cup * 0.45 * y * y + fold * 0.35 * abs(y) - droop * 0.5 * x * x + wob * x * y
        p = base + (A * x + S * y + N * z) * L
        return push(p)[0]

    center = b.vert(place(0.0, 0.0), w, dist, 1.0)
    b.uv_of[center] = (0.5, 0.0)
    b.col_of[center] = col
    # normalise so the blade length (base -> tip) is 1
    tip = _outline(0.0, P)
    rings = []
    for k in range(1, N_RING + 1):
        t = k / N_RING
        ring = []
        for j in range(N_ANG):
            phi = -math.pi + math.tau * (j + 0.5) / N_ANG
            r = _outline(phi, P) / tip * t
            x, y = math.cos(phi) * r, math.sin(phi) * r * width
            v = b.vert(place(x, y), w, dist, 1.0)
            b.uv_of[v] = (0.5 + y * 0.5, x)
            b.col_of[v] = col
            ring.append(v)
        rings.append(ring)
    first = rings[0]
    for j in range(N_ANG):
        b.faces.append((center, first[j], first[(j + 1) % N_ANG]))
    for r0, r1 in zip(rings[:-1], rings[1:]):
        for j in range(N_ANG):
            j2 = (j + 1) % N_ANG
            b.faces.append((r0[j], r1[j], r1[j2], r0[j2]))


def build(b, tree, radii, owner, params, scale, push, rng, node_weights, node_dist, r_ref):
    """Add leaves to MeshBuilder `b`. owner[i] picks the settings in params for node i.
    r_ref[k]: stem radius of point k at which leaves reach full size. Returns the number of leaves."""
    count = 0
    for seq in colonize.chains(tree):
        if tree.kind[seq[-1]] == 2:
            continue  # no leaves on tendrils
        acc = 0.0
        side = 1.0 if rng.random() < 0.5 else -1.0
        nxt = None
        for a, i in zip(seq[:-1], seq[1:]):
            P = params[owner[i]]
            if P.leaf_chance <= 0.0:
                continue
            spacing = max(P.leaf_spacing * scale, 1e-6)
            if nxt is None:
                nxt = spacing * rng.uniform(0.2, 1.0)
            acc += (tree.pos[i] - tree.pos[a]).length
            if acc < nxt:
                continue
            nxt = acc + spacing * rng.uniform(0.7, 1.3)
            side = -side
            if rng.random() > P.leaf_chance:
                continue
            count += 1
            _leaf(b, tree, radii, i, side, P, scale, push, rng, node_weights(i), node_dist(i), r_ref[owner[i]])
    return count


def _leaf(b, tree, radii, i, side, P, scale, push, rng, w, dist, r_ref):
    stem = tree.pos[i]
    t = tree.dir[i]
    nb = tree.normal[i]
    s = t.cross(nb)
    s = s.normalized() if s.length_squared > 1e-12 else nb.orthogonal().normalized()
    rel = min(1.0, radii[i] / max(r_ref, 1e-9))
    size = 1.0 - P.leaf_tip_small * (1.0 - rel)
    L = P.leaf_size * scale * max(0.15, size) * (1.0 + P.leaf_size_var * rng.uniform(-1.0, 1.0))
    if L <= 0.0:
        return
    young = max(0.0, min(1.0, 1.0 - size + rng.uniform(-0.15, 0.15)))
    c0, c1 = Vector(P.leaf_color), Vector(P.leaf_color_young)
    col = c0.lerp(c1, young) * rng.uniform(0.85, 1.15)
    col = (min(col.x, 1.0), min(col.y, 1.0), min(col.z, 1.0), 1.0)

    # petiole: out to the side and away from the body
    pd = (s * side * 0.8 + nb * 0.9 + t * 0.3 + colonize._rand_unit(rng) * 0.3).normalized()
    pl = L * P.leaf_petiole
    base = stem + pd * pl
    base, _ = push(base)
    if pl > 1e-6:
        mid = stem.lerp(base, 0.5) + nb * pl * 0.15
        pts = [stem, push(mid)[0], base]
        rr = max(radii[i] * 0.55, L * 0.018)
        tube(b, pts, [nb, nb, nb], [rr, rr * 0.9, rr * 0.8], [w] * 3, [dist] * 3, [0.0] * 3, 4)
        for v in range(len(b.verts) - 12, len(b.verts)):
            b.col_of[v] = col

    # blade: midrib roughly continues the petiole, face turned toward the outside
    flat = pd - nb * pd.dot(nb)
    flat = flat.normalized() if flat.length_squared > 1e-10 else s
    rnd = colonize._rand_unit(rng)
    N = (nb * (0.4 + P.leaf_face) + rnd * (1.2 - P.leaf_face)).normalized()
    A = flat + t * rng.uniform(-0.3, 0.5)
    A = A - N * A.dot(N)
    if A.length_squared < 1e-10:
        A = N.orthogonal()
    A.normalize()
    A = Matrix.Rotation(rng.uniform(-0.3, 0.3), 3, N) @ A
    S = N.cross(A).normalized()
    _blade(b, base, A, S, N, L, P, push, w, dist, col, rng)
