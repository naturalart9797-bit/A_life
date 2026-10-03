# SPDX-License-Identifier: GPL-3.0-or-later
"""Skeleton-driven hand retopology.

Guides are clicked on the *back* of the hand: the wrist, and for every digit
its base knuckle, the two joints and the fingertip. From each guide a ray is
shot through the hand to find the bone axis and thickness. The mesh is then
built ring by ring around those axes (8-sided finger tubes with three loops at
every joint, a palm grid whose knuckle end splits into the four finger bases,
the thumb grown out of the side of the palm) and every vertex is placed by
casting a ray from the axis to the skin, so fingers that touch never steal
each other's vertices.
"""

import math

import numpy as np

FINGERS = ["thumb", "index", "middle", "ring", "pinky"]
_FINGER_JA = {"thumb": "親指", "index": "人差し指", "middle": "中指",
              "ring": "薬指", "pinky": "小指"}
_FINGER_EN = {"thumb": "Thumb", "index": "Index", "middle": "Middle",
              "ring": "Ring", "pinky": "Pinky"}
_JOINT_JA = ["付け根の関節", "第2関節", "第1関節", "指先"]
_THUMB_JA = ["付け根（手首寄りの膨らみ）", "付け根の関節", "関節", "指先"]
_JOINT_EN = ["base knuckle", "middle joint", "end joint", "tip"]

def landmarks():
    """(id, Japanese, English) in click order. All on the back of the hand."""
    out = [("wrist", "手首（手の甲側の中央）", "Wrist, back of the hand, centre")]
    for f in FINGERS:
        names = _THUMB_JA if f == "thumb" else _JOINT_JA
        for j in range(4):
            out.append((f"{f}_{j}", f"{_FINGER_JA[f]}：{names[j]}（甲側）",
                        f"{_FINGER_EN[f]}: {_JOINT_EN[j]} (back side)"))
    return out


def _norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


class _Builder:

    def __init__(self, surf):
        self.surf = surf
        self.verts = []
        self.faces = []

    def add(self, p):
        self.verts.append(np.asarray(p, float))
        return len(self.verts) - 1

    def cast(self, origin, direction, fallback_dist, max_factor=4.0):
        """Skin point from ``origin`` along ``direction``. Hits further than
        ``fallback_dist * max_factor`` are ignored (e.g. a sideways ray near a
        finger base escaping into the neighbouring finger); the expected
        point is snapped to the surface instead."""
        d = _norm(direction)
        hit = self.surf.ray(origin, d, fallback_dist * max_factor)
        if hit is not None:
            return hit
        p = origin + d * fallback_dist
        return self.surf.nearest(p)

    def bridge(self, a, b):
        """Quads between two closed rings of equal size, matching order."""
        n = len(a)
        A = np.array([self.verts[i] for i in a])
        best = None
        for rev in (False, True):
            bb = list(reversed(b)) if rev else list(b)
            B = np.array([self.verts[i] for i in bb])
            for off in range(n):
                cost = np.linalg.norm(A - np.roll(B, -off, axis=0), axis=1).sum()
                if best is None or cost < best[0]:
                    best = (cost, bb[off:] + bb[:off])
        bb = best[1]
        for k in range(n):
            self.faces.append((a[k], a[(k + 1) % n], bb[(k + 1) % n], bb[k]))
        return bb

    def cast_hit(self, origin, direction, dist):
        """(point, hit?) -- like cast, but tells whether the skin was hit."""
        d = _norm(direction)
        hit = self.surf.ray(origin, d, dist)
        if hit is not None:
            return hit, True
        return self.surf.nearest(origin + d * dist * 0.5), False

    def inset(self, grid, amount=0.3):
        """Inset the quads of a vertex grid (rows x cols) as one region: a
        loop of quads appears around it with poles at its corners -- the
        oval "eye" loop modellers put on knuckles."""
        R, C = len(grid), len(grid[0])
        region = set()
        for i in range(R - 1):
            for j in range(C - 1):
                region.add(frozenset((grid[i][j], grid[i][j + 1],
                                      grid[i + 1][j + 1], grid[i + 1][j])))
        self.faces = [f for f in self.faces if frozenset(f) not in region]
        boundary = ([grid[0][j] for j in range(C)] + [grid[i][C - 1] for i in range(1, R)]
                    + [grid[R - 1][j] for j in range(C - 2, -1, -1)]
                    + [grid[i][0] for i in range(R - 2, 0, -1)])
        centre = np.mean([self.verts[v] for row in grid for v in row], axis=0)
        inner = {}
        for v in boundary:
            p = self.verts[v] + (centre - self.verts[v]) * amount
            inner[v] = self.add(self.surf.nearest(p))
        new_grid = [[inner.get(v, v) for v in row] for row in grid]
        for i in range(R - 1):
            for j in range(C - 1):
                self.faces.append((new_grid[i][j], new_grid[i][j + 1],
                                   new_grid[i + 1][j + 1], new_grid[i + 1][j]))
        n = len(boundary)
        for k in range(n):
            a, b = boundary[k], boundary[(k + 1) % n]
            self.faces.append((a, b, inner[b], inner[a]))


def _axis_from_guide(surf, p, default_r, along=None):
    """Bone axis point under a guide clicked on the back of the hand. The ray
    goes straight through the digit (perpendicular to ``along``, the bone
    direction), so knuckles on a slope do not shift the axis."""
    n = surf.normal(p)
    if along is not None:
        a = _norm(along)
        m = n - a * n.dot(a)
        if np.linalg.norm(m) > 0.3:
            n = _norm(m)
    hit = surf.ray(p - n * default_r * 0.05, -n, default_r * 6.0)
    if hit is not None and np.linalg.norm(hit - p) > default_r * 0.2:
        return (p + hit) * 0.5, np.linalg.norm(hit - p) * 0.5, n
    return p - n * default_r, default_r, n


def _dorsal_window(B, ring, centre, up, width):
    """Start index of the ``width`` consecutive ring verts facing ``up``."""
    n = len(ring)
    score = [float(np.dot(_norm(B.verts[v] - centre), up)) for v in ring]
    best = max(range(n), key=lambda s: sum(score[(s + t) % n] for t in range(width)))
    return [(best + t) % n for t in range(width)]


def build_hand(guides, surf, segments=12, forearm=3, knuckle_loops=True):
    """``surf`` provides ray(origin, dir, dist) -> point|None, nearest(p) and
    normal(p). ``segments`` = edges around a finger (8 or 12).
    Returns (verts (N, 3), faces)."""
    g = {k: np.asarray(v, float) for k, v in guides.items()}
    need = [lid for lid, _ja, _en in landmarks()]
    missing = [k for k in need if k not in g]
    if missing:
        raise ValueError("missing guides: " + ", ".join(missing))
    w = max(2, int(segments) // 4)          # cells per side of a finger
    N = 4 * w                               # verts around a finger
    blocks = [(i * (w + 1), i * (w + 1) + w) for i in range(4)]
    gaps = [blocks[i][1] for i in range(3)]  # web strip between gap and gap + 1
    ncols = 4 * w + 4                        # dorsal row verts (4 blocks + 3 webs)
    palm_rings = 2 * w + 4

    hand_len = np.linalg.norm(g["middle_3"] - g["wrist"])
    r0 = hand_len * 0.045
    B = _Builder(surf)

    axes = {"wrist": _axis_from_guide(surf, g["wrist"], r0 * 2.0)}
    for f in FINGERS:
        for j in range(3):
            along = g[f"{f}_{j + 1}"] - g[f"{f}_{max(j - 1, 0)}"] if j else g[f"{f}_1"] - g[f"{f}_0"]
            axes[f"{f}_{j}"] = _axis_from_guide(surf, g[f"{f}_{j}"], r0, along)

    # ---- palm frame ----------------------------------------------------
    W, w_r, w_n = axes["wrist"]
    K = [axes[f + "_0"][0] for f in FINGERS[1:]]
    Y = _norm(np.mean(K, axis=0) - W)
    X = K[3] - K[0]
    X = _norm(X - Y * X.dot(Y))
    Z = np.cross(X, Y)
    if Z.dot(w_n) < 0:
        Z = -Z
    palm_len = (np.mean(K, axis=0) - W).dot(Y)

    # Knuckle line columns: each finger block spans +-0.36 of the spacing to
    # its neighbours around the finger axis; the rest is the web strip.
    knuck = np.zeros((ncols, 3))
    for i, (c0, c1) in enumerate(blocks):
        left = K[i] - (K[i + 1] - K[i] if i == 0 else K[i] - K[i - 1]) * 0.36
        right = K[i] + (K[i] - K[i - 1] if i == 3 else K[i + 1] - K[i]) * 0.36
        for k in range(w + 1):
            knuck[c0 + k] = left + (right - left) * k / w

    lo = B.cast(W, -X, w_r * 3)
    hi = B.cast(W, X, w_r * 3)
    wl, wh = (lo - W).dot(-X), (hi - W).dot(X)
    wrist_cols = np.array([W + X * x for x in np.linspace(-wl * 0.8, wh * 0.8, ncols)])
    finger_thick = np.mean([axes[f + "_0"][1] for f in FINGERS[1:]]) * 1.2

    def palm_ring(cols, thick):
        """Ring around the palm: dorsal row, pinky side, palmar row
        (reversed), index side. Returns (ring, index-side column dorsal ->
        palmar, hit ratio)."""
        hits = 0
        dorsal, palmar = [], []
        for c in range(ncols):
            p, h = B.cast_hit(cols[c], Z, thick * 1.6)
            dorsal.append(B.add(p))
            q, h2 = B.cast_hit(cols[c], -Z, thick * 1.6)
            palmar.append(B.add(q))
            hits += h + h2
        sides = []
        for edge, sx in ((cols[-1], 1.0), (cols[0], -1.0)):
            col = []
            for j in range(1, w):
                phi = math.pi * j / w
                d = Z * math.cos(phi) + X * sx * math.sin(phi)
                p, h = B.cast_hit(edge, d, thick * 2.0)
                hits += h
                col.append(B.add(p))
            sides.append(col)
        pinky_side, index_side = sides
        ring = dorsal + pinky_side + palmar[::-1] + index_side[::-1]
        index_col = [dorsal[0]] + index_side + [palmar[0]]
        return ring, index_col, hits / (2 * ncols + 2 * (w - 1)), dorsal, palmar, pinky_side

    rings = []        # all rings, wrist/forearm first
    dt = 1.0 / (palm_rings - 1)
    # Forearm rings (only as far as the scan goes).
    fore = []
    for k in range(1, forearm + 1):
        cols = wrist_cols - Y * (k * dt * palm_len)
        res = palm_ring(cols, w_r)
        if res[2] < 0.8:
            break
        fore.append(res)
    for res in reversed(fore):
        rings.append(res)
    first_palm = len(rings)
    for ri in range(palm_rings):
        t = ri * dt
        cols = wrist_cols * (1 - t) + knuck * t
        rings.append(palm_ring(cols, w_r * (1 - t) + finger_thick * t))
    infos = rings

    # Thumb base block (w x w cells) on the index side of the palm.
    tb = g["thumb_0"]
    t_rel = (tb - W).dot(Y) / max(palm_len, 1e-9)
    jc = int(round(t_rel * (palm_rings - 1) - w / 2.0)) + first_palm
    jc = int(np.clip(jc, first_palm + 1, len(rings) - 2 - w))
    block = [infos[jc + i][1] for i in range(w + 1)]          # rows = rings
    removed = {block[i][j] for i in range(1, w) for j in range(1, w)}
    for ri in range(len(rings) - 1):
        a, b = rings[ri][0], rings[ri + 1][0]
        n = len(a)
        for k in range(n):
            quad = (a[k], a[(k + 1) % n], b[(k + 1) % n], b[k])
            if removed.intersection(quad):
                continue
            if all(v in {x for row in block for x in row} for v in quad):
                continue
            B.faces.append(quad)
    thumb_base = (list(block[0]) + [block[i][w] for i in range(1, w + 1)]
                  + list(reversed(block[w]))[1:] + [block[i][0] for i in range(w - 1, 0, -1)])

    # ---- finger bases and web strips (knuckle end of the palm) -----------
    _ring, index_col, _h, dors, palmr, pinky_side = infos[-1]
    finger_dirs = {f: _norm(axes[f + "_1"][0] - axes[f + "_0"][0]) for f in FINGERS}
    names = FINGERS[1:]
    side_cols = {0: index_col[1:-1], ncols - 1: pinky_side}   # dorsal -> palmar
    for gi, c in enumerate(gaps):
        fa, fb = names[gi], names[gi + 1]
        pa = axes[fa + "_0"][0] * 0.55 + axes[fa + "_1"][0] * 0.45
        pb = axes[fb + "_0"][0] * 0.55 + axes[fb + "_1"][0] * 0.45
        d = -_norm(finger_dirs[fa] + finger_dirs[fb])
        thick = (axes[fa + "_0"][1] + axes[fb + "_0"][1]) * 0.5
        for cc, wt in ((c, 0.35), (c + 1, 0.65)):
            col = []
            for j in range(1, w):
                o = pa * (1 - wt) + pb * wt + Z * thick * 0.8 * (1 - 2 * j / w)
                col.append(B.add(B.cast(o, d, np.linalg.norm(o - knuck[cc]) * 1.5)))
            side_cols[cc] = col
        # Web strip between the two fingers (faces looking down the gap).
        left = [dors[c]] + side_cols[c] + [palmr[c]]
        right = [dors[c + 1]] + side_cols[c + 1] + [palmr[c + 1]]
        for j in range(w):
            B.faces.append((left[j], right[j], right[j + 1], left[j + 1]))
    base_rings = {}
    for i, f in enumerate(names):
        c0, c1 = blocks[i]
        base_rings[f] = ([dors[c] for c in range(c0, c1 + 1)] + side_cols[c1]
                         + [palmr[c] for c in range(c1, c0 - 1, -1)]
                         + list(reversed(side_cols[c0])))
    base_rings["thumb"] = thumb_base

    # ---- finger tubes ------------------------------------------------------
    offset = 0.5 if w % 2 else 0.0
    insets = []
    for f in FINGERS:
        pts = [axes[f"{f}_{j}"][0] for j in range(3)] + [g[f + "_3"]]
        radii = [axes[f"{f}_{j}"][1] for j in range(3)]
        up = _norm(sum(axes[f"{f}_{j}"][2] for j in range(3)))
        seg = [np.linalg.norm(pts[j + 1] - pts[j]) for j in range(3)]
        dl = 0.2 * min(seg)

        def at(j, frac):
            return pts[j] + (pts[j + 1] - pts[j]) * frac, j, frac

        # Dense loops at every joint: base knuckle, middle and end joints.
        stations = [at(0, dl / seg[0]), at(0, 0.5)]
        joint_idx = []
        for j in (1, 2):
            stations.append(at(j - 1, 1.0 - dl / max(seg[j - 1], 1e-9)))
            joint_idx.append(len(stations) + 1)   # index into ``aligned``
            stations.append((pts[j], j, 0.0))
            stations.append(at(j, dl / max(seg[j], 1e-9)))
            if j == 1:
                stations.append(at(1, 0.5))
        stations.append(at(2, 0.5))
        stations.append(at(2, 0.8))

        aligned = [base_rings[f]]
        centres = [None]
        for c, j, _frac in stations:
            if j == 0:
                tan = pts[1] - pts[0]
            elif j >= 2 and _frac > 0.0:
                tan = pts[3] - pts[2]
            else:
                tan = _norm(pts[j] - pts[j - 1]) + _norm(pts[j + 1] - pts[j])
            tan = _norm(tan)
            u = _norm(up - tan * up.dot(tan))
            s = np.cross(tan, u)
            r = radii[min(j, 2)]
            ring = []
            for k in range(N):
                th = (k + offset) * 2 * math.pi / N
                ring.append(B.add(B.cast(c, math.cos(th) * u + math.sin(th) * s, r, 1.6)))
            aligned.append(B.bridge(aligned[-1], ring))
            centres.append(c)

        # Oval knuckle loops on the back of each joint.
        if knuckle_loops:
            for ji in joint_idx:
                cols = _dorsal_window(B, aligned[ji], centres[ji], up, w + 1)
                insets.append([[aligned[r][c] for c in cols] for r in (ji - 1, ji, ji + 1)])
            if f != "thumb":
                i = names.index(f)
                c0, c1 = blocks[i]
                prev_ring = infos[-2]
                prev_dors = prev_ring[3]
                insets.append([[prev_dors[c] for c in range(c0, c1 + 1)],
                               aligned[0][0:w + 1], aligned[1][0:w + 1]])

        # Fingertip cap: w x w grid closing the last ring.
        last = aligned[-1]
        tip_c = centres[-1]
        tan = _norm(pts[3] - pts[2])
        reach = np.linalg.norm(pts[3] - tip_c)
        grid = [[None] * (w + 1) for _ in range(w + 1)]
        bpos = []
        for k in range(w + 1):
            bpos.append((k, 0))
        for k in range(1, w + 1):
            bpos.append((w, k))
        for k in range(w - 1, -1, -1):
            bpos.append((k, w))
        for k in range(w - 1, 0, -1):
            bpos.append((0, k))
        for (i, j), v in zip(bpos, last):
            grid[i][j] = v
        P = lambda i, j: B.verts[grid[i][j]]
        for i in range(1, w):
            for j in range(1, w):
                s_, t_ = i / w, j / w
                co = ((1 - t_) * P(i, 0) + t_ * P(i, w) + (1 - s_) * P(0, j) + s_ * P(w, j)
                      - ((1 - s_) * (1 - t_) * P(0, 0) + s_ * (1 - t_) * P(w, 0)
                         + (1 - s_) * t_ * P(0, w) + s_ * t_ * P(w, w)))
                d = _norm(co - tip_c + tan * reach)
                grid[i][j] = B.add(B.cast(tip_c, d, reach * 1.2))
        for i in range(w):
            for j in range(w):
                B.faces.append((grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1]))

    for grid in insets:
        B.inset(grid)

    V = np.array(B.verts)
    used = sorted({i for f in B.faces for i in f})
    remap = {old: new for new, old in enumerate(used)}
    faces = [tuple(remap[i] for i in f) for f in B.faces]
    V = V[used]
    from .fit import orient_faces
    return V, orient_faces(V, faces, surf.normal)
