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

PALM_RINGS = 8
# Knuckle end of the palm, per row: 2 segments per finger plus a 1-segment
# web strip between neighbouring fingers -> 4 * 2 + 3 = 11 segments.
DORSAL_COLS = 12
BLOCKS = [(0, 2), (3, 5), (6, 8), (9, 11)]   # finger base columns
GAPS = [2, 5, 8]                             # web strips (c, c + 1)


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


def build_hand(guides, surf):
    """``surf`` provides ray(origin, dir, dist) -> point|None, nearest(p) and
    normal(p). Returns (verts (N, 3), faces)."""
    g = {k: np.asarray(v, float) for k, v in guides.items()}
    need = [lid for lid, _ja, _en in landmarks()]
    missing = [k for k in need if k not in g]
    if missing:
        raise ValueError("missing guides: " + ", ".join(missing))

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

    # Knuckle line columns: each finger block spans +-0.36 of the spacing
    # to its neighbours around the finger axis; the rest is the web strip.
    knuck = np.zeros((DORSAL_COLS, 3))
    for i, (c0, c1) in enumerate(BLOCKS):
        left = K[i] - (K[i + 1] - K[i] if i == 0 else K[i] - K[i - 1]) * 0.36
        right = K[i] + (K[i] - K[i - 1] if i == 3 else K[i + 1] - K[i]) * 0.36
        knuck[c0], knuck[c0 + 1], knuck[c1] = left, K[i], right

    def side_width(origin):
        lo = B.cast(origin, -X, w_r * 3)
        hi = B.cast(origin, X, w_r * 3)
        return (lo - origin).dot(-X), (hi - origin).dot(X)

    wl, wh = side_width(W)
    wrist_cols = np.array([W + X * x for x in np.linspace(-wl * 0.8, wh * 0.8, DORSAL_COLS)])

    # Palm rings: dorsal 0..8, pinky side, palmar 8..0, index side (20 verts).
    palm = []
    side_ids = []  # (index-side corner dorsal, mid, palmar) per ring
    for ri in range(PALM_RINGS):
        t = ri / (PALM_RINGS - 1)
        cols = wrist_cols * (1 - t) + knuck * t
        centre = (cols[5] + cols[6]) * 0.5
        thick = w_r * (1 - t) + np.mean([axes[f + "_0"][1] for f in FINGERS[1:]]) * t * 1.2
        dorsal = []
        palmar = []
        for c in range(DORSAL_COLS):
            o = cols[c]
            dorsal.append(B.add(B.cast(o, Z, thick * 1.5)))
            palmar.append(B.add(B.cast(o, -Z, thick * 1.5)))
        pinky_mid = B.add(B.cast(centre, X, np.linalg.norm(cols[-1] - centre) * 1.3))
        index_mid = B.add(B.cast(centre, -X, np.linalg.norm(cols[0] - centre) * 1.3))
        ring = dorsal + [pinky_mid] + palmar[::-1] + [index_mid]
        palm.append(ring)
        side_ids.append((dorsal[0], index_mid, palmar[0]))

    # Thumb base block on the index side of the palm.
    tb = g["thumb_0"]
    ring_t = [((tb - W).dot(Y)) / max((np.mean(K, axis=0) - W).dot(Y), 1e-9)]
    jc = int(round(np.clip(ring_t[0] * (PALM_RINGS - 1), 1, PALM_RINGS - 2)))
    hole_v = side_ids[jc][1]
    for ri in range(PALM_RINGS - 1):
        a, b = palm[ri], palm[ri + 1]
        n = len(a)
        for k in range(n):
            quad = (a[k], a[(k + 1) % n], b[(k + 1) % n], b[k])
            if hole_v in quad:
                continue
            B.faces.append(quad)
    s0, s1, s2 = side_ids[jc - 1], side_ids[jc], side_ids[jc + 1]
    thumb_base = [s0[0], s0[1], s0[2], s1[2], s2[2], s2[1], s2[0], s1[0]]

    # ---- finger base rings (knuckle end of the palm) ---------------------
    last = palm[-1]
    dors = last[0:DORSAL_COLS]
    palmr = last[DORSAL_COLS + 1:2 * DORSAL_COLS + 1][::-1]
    pinky_mid = last[DORSAL_COLS]
    index_mid = last[-1]
    finger_dirs = {f: _norm(axes[f + "_1"][0] - axes[f + "_0"][0]) for f in FINGERS}
    names = FINGERS[1:]
    mids = {0: index_mid, DORSAL_COLS - 1: pinky_mid}
    for gi, c in enumerate(GAPS):
        fa, fb = names[gi], names[gi + 1]
        pa = axes[fa + "_0"][0] * 0.55 + axes[fa + "_1"][0] * 0.45
        pb = axes[fb + "_0"][0] * 0.55 + axes[fb + "_1"][0] * 0.45
        d = -_norm(finger_dirs[fa] + finger_dirs[fb])
        for cc, w in ((c, 0.35), (c + 1, 0.65)):
            o = pa * (1 - w) + pb * w
            mids[cc] = B.add(B.cast(o, d, np.linalg.norm(o - knuck[cc]) * 1.5))
        # Web strip between the two fingers (faces looking down the gap).
        B.faces.append((palmr[c], palmr[c + 1], mids[c + 1], mids[c]))
        B.faces.append((mids[c], mids[c + 1], dors[c + 1], dors[c]))
    base_rings = {}
    for i, f in enumerate(names):
        c0, c1 = BLOCKS[i]
        base_rings[f] = [dors[c0], dors[c0 + 1], dors[c1], mids[c1],
                         palmr[c1], palmr[c0 + 1], palmr[c0], mids[c0]]
    base_rings["thumb"] = thumb_base

    # ---- finger tubes ------------------------------------------------------
    for f in FINGERS:
        pts = [axes[f"{f}_{j}"][0] for j in range(3)] + [g[f + "_3"]]
        radii = [axes[f"{f}_{j}"][1] for j in range(3)]
        up = _norm(sum(axes[f"{f}_{j}"][2] for j in range(3)))
        seg = [np.linalg.norm(pts[j + 1] - pts[j]) for j in range(3)]

        def at(j, frac):
            return pts[j] + (pts[j + 1] - pts[j]) * frac, j, frac

        # Base knuckle loop just past the palm, then the proximal segment.
        stations = [at(0, 0.2), at(0, 0.5)]
        for j in (1, 2):
            dl = 0.18 * seg[j - 1]
            stations.append(at(j - 1, 1.0 - dl / max(seg[j - 1], 1e-9)))
            stations.append((pts[j], j, 0.0))
            stations.append(at(j, 0.18 * seg[j - 1] / max(seg[j], 1e-9)))
        stations.append(at(2, 0.55))
        stations.append(at(2, 0.85))

        prev = base_rings[f]
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
            for k in range(8):
                th = k * math.pi / 4
                ring.append(B.add(B.cast(c, math.cos(th) * u + math.sin(th) * s, r, 1.6)))
            prev = B.bridge(prev, ring)
        # fingertip cap: 4 quads around a centre vertex
        tip_c = stations[-1][0]
        tan = _norm(pts[3] - pts[2])
        centre = B.add(B.cast(tip_c, tan, np.linalg.norm(pts[3] - tip_c) * 1.5))
        for k in range(0, 8, 2):
            B.faces.append((prev[k], prev[(k + 1) % 8], prev[(k + 2) % 8], centre))

    V = np.array(B.verts)
    used = sorted({i for f in B.faces for i in f})
    remap = {old: new for new, old in enumerate(used)}
    faces = [tuple(remap[i] for i in f) for f in B.faces]
    V = V[used]
    from .fit import orient_faces
    return V, orient_faces(V, faces, surf.normal)
