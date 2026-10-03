# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural face-mask template with production-style topology.

Half face (u >= 0, u = 0 is the centre line) laid out as quad patches:
concentric loops around the eye opening and around the mouth opening,
a nasolabial pole beside the nose and poles at the inner eye corner and
mouth corner -- the usual layout of a hand-made face retopology. The half
is mirrored to a full mask.

Layout space: u right, v up (chin tip v = -1, under the chin below that,
top of mask v = 1), half width about 1. ``canonical`` gives a rough 3D face
so landmark warping starts from a face-like shape.
"""

import math

import numpy as np

from .patches import Layout, bezier, ellipse_arc

EYE_C = (0.38, 0.22)
EYE_IN = (0.12, 0.05)    # eye opening radii
EYE_OUT = (0.22, 0.17)   # outer edge of the eye loops
NOSTRIL_C = (0.145, -0.115)
NOSTRIL_IN = (0.045, 0.028)   # nostril opening radii
NOSTRIL_OUT = (0.08, 0.06)    # nostril rim loops end here
MOUTH_C = (0.0, -0.45)
MOUTH_IN = (0.20, 0.035)
MOUTH_OUT = (0.32, 0.18)
MU_ANGLE = math.radians(55)   # upper-lip split of the mouth loops

H = math.pi / 2


def _ring(L, centre, outer, radii_in, radii_out, pre_in, pre_out):
    """Two loops of patches around an opening inside the polygon ``outer``
    (names of layout points): spokes from every polygon corner to an outer
    ring corner, then to the opening."""
    cx, cy = centre
    ang = {}
    for c in outer:
        x, y = L.points[c]
        ang[c] = math.atan2((y - cy) / radii_out[1], (x - cx) / radii_out[0])
    order = sorted(outer, key=lambda c: ang[c])
    for pre, (rx, ry) in ((pre_in, radii_in), (pre_out, radii_out)):
        for c in outer:
            L.point(pre + c, (cx + rx * math.cos(ang[c]), cy + ry * math.sin(ang[c])))
        for k in range(len(order)):
            a, b = order[k], order[(k + 1) % len(order)]
            a0, a1 = ang[a], ang[b]
            if a1 <= a0:
                a1 += 2 * math.pi
            L.curve(pre + a, pre + b, ellipse_arc(centre, rx, ry, a0, a1))
    n = len(outer)
    for k in range(n):
        a, b = outer[k], outer[(k + 1) % n]
        L.patch(a, b, pre_out + b, pre_out + a)
        L.patch(pre_out + a, pre_out + b, pre_in + b, pre_in + a)


def _coons(L, a, b, c, d):
    """Coons map X(s, t) of the patch (a, b, c, d): s along a->b, t along a->d."""
    c0, c1 = L._curve(a, b), L._curve(d, c)
    d0, d1 = L._curve(a, d), L._curve(b, c)
    pa, pb, pc, pd = (L.points[x] for x in (a, b, c, d))

    def X(s, t):
        return ((1 - t) * c0(s) + t * c1(s) + (1 - s) * d0(t) + s * d1(t)
                - ((1 - s) * (1 - t) * pa + s * (1 - t) * pb
                   + (1 - s) * t * pd + s * t * pc))
    return X


def _strip(L, a, b, c, d, levels):
    """Split the strip (a, b, c, d) into len(levels) + 1 sub-strips across t
    (inner side a-b, outer side d-c). Points on the shared spokes a-d / b-c
    are named after the spoke, so neighbouring strips line up. Returns the
    sub-patch corner tuples, inner first."""
    X = _coons(L, a, b, c, d)

    def spoke_pt(p, q, k, t):
        name = f"{p}>{q}{k}"
        if name not in L.points:
            L.point(name, L._curve(p, q)(t))
        return name

    left = [a] + [spoke_pt(a, d, k + 1, t) for k, t in enumerate(levels)] + [d]
    right = [b] + [spoke_pt(b, c, k + 1, t) for k, t in enumerate(levels)] + [c]
    ts = [0.0] + list(levels) + [1.0]
    for k in range(len(ts) - 1):
        L.curve(left[k], left[k + 1], _sub(L._curve(a, d), ts[k], ts[k + 1]))
        L.curve(right[k], right[k + 1], _sub(L._curve(b, c), ts[k], ts[k + 1]))
    for k, t in enumerate(levels):
        L.curve(left[k + 1], right[k + 1], (lambda tt: (lambda s_: X(s_, tt)))(t))
    return [(left[k], right[k], right[k + 1], left[k + 1]) for k in range(len(ts) - 1)]


def _sub(fn, t0, t1):
    return lambda t: fn(t0 + (t1 - t0) * t)


HEAD_LEVELS = (0.3, 0.37, 0.68)   # inner | transition | ear level | outer


def _head(L):
    """Rest of the head around the face mask: spokes from the mask outline
    to the back centre line / neck bottom, filled with strips whose loops run
    around the face (like a hand-made head), an ear socket on the side."""
    L.tag = "head"
    L.point("CR", (0.0, 1.6))     # crown
    L.point("BH", (0.0, 2.4))     # back of the head
    L.point("NP", (0.0, 3.2))     # nape (top of the neck, back)
    L.point("NB", (0.0, 3.9))     # bottom of the neck, back
    L.point("NF", (0.0, -2.1))    # bottom of the neck, front
    L.point("NA", (1.9, -1.9))    # bottom of the neck, side
    L.curve("OT", "BH", bezier((0.75, 1.0), (1.2, 1.9), (0.0, 2.4)))
    L.curve("OR", "NP", bezier((0.92, 0.25), (2.0, 2.0), (0.0, 3.2)))
    L.curve("OJ", "NB", bezier((0.82, -0.5), (3.0, 2.0), (0.0, 3.9)))
    L.curve("OJ2", "NA", bezier((0.7, -0.95), (1.4, -1.3), (1.9, -1.9)))
    L.curve("NB", "NA", bezier((0.0, 3.9), (4.0, 2.4), (1.9, -1.9)))
    L.curve("NA", "NF", bezier((1.9, -1.9), (1.0, -2.4), (0.0, -2.1)))
    strips = [("F", "OT", "BH", "CR"), ("OT", "OR", "NP", "BH"), ("OR", "OJ", "NB", "NP"),
              ("OJ", "OJ2", "NA", "NB"), ("OJ2", "C2", "NF", "NA")]
    for si, st in enumerate(strips):
        subs = _strip(L, *st, HEAD_LEVELS)
        for k, sub in enumerate(subs):
            if k == 1:
                # Twice the segments from here on, so the back of the head
                # and the neck are as dense as the forehead.
                L.tpatch(*sub)
            elif si == 2 and k == 2:
                # Ear socket: two loops around the ear opening.
                q0, q1, q2, q3 = sub
                X = _coons(L, *sub)
                ctr = X(0.5, 0.5)
                w = np.linalg.norm(X(1.0, 0.5) - X(0.0, 0.5))
                h = np.linalg.norm(X(0.5, 1.0) - X(0.5, 0.0))
                r = 0.42 * min(w, h)
                L.tag = "ear"
                _ring(L, tuple(ctr), [q0, q1, q2, q3], (r * 0.45, r * 0.6), (r, r * 1.2),
                      "x", "y")
                L.tag = "head"
            else:
                L.patch(*sub)
    L.tag = None
    return L


def _layout(head=False):
    """Half-face quad layout (u >= 0). Every centre-line point has exactly one
    edge into each half, so there are no 6+ poles on the centre line; the few
    poles elsewhere have valence 3 or 5 (pentagon patches)."""
    L = Layout()
    ex, ey = EYE_C
    mx, my = MOUTH_C
    # Eye: inner (opening) and outer ring corners. R = outer corner (temple
    # side), L = inner corner (nose side), T = upper lid, B = lower lid.
    for pre, (rx, ry) in (("e", EYE_IN), ("E", EYE_OUT)):
        L.point(pre + "R", (ex + rx, ey))
        L.point(pre + "T", (ex, ey + ry))
        L.point(pre + "L", (ex - rx, ey))
        L.point(pre + "B", (ex, ey - ry))
        for a, b, a0, a1 in (("R", "T", 0, H), ("T", "L", H, 2 * H),
                             ("L", "B", 2 * H, 3 * H), ("B", "R", 3 * H, 4 * H)):
            L.curve(pre + a, pre + b, ellipse_arc(EYE_C, rx, ry, a0, a1))
    # Mouth (half): T / B on the centre line, U = upper lip split, R = corner.
    for pre, (rx, ry) in (("m", MOUTH_IN), ("M", MOUTH_OUT)):
        L.point(pre + "T", (mx, my + ry))
        L.point(pre + "U", (mx + rx * math.cos(MU_ANGLE), my + ry * math.sin(MU_ANGLE)))
        L.point(pre + "R", (mx + rx, my))
        L.point(pre + "B", (mx, my - ry))
        L.curve(pre + "U", pre + "T", ellipse_arc(MOUTH_C, rx, ry, MU_ANGLE, H))
        L.curve(pre + "R", pre + "U", ellipse_arc(MOUTH_C, rx, ry, 0, MU_ANGLE))
        L.curve(pre + "B", pre + "R", ellipse_arc(MOUTH_C, rx, ry, -H, 0))

    # Centre line
    L.point("F", (0.0, 1.0))      # top of mask
    L.point("N1", (0.0, 0.45))    # glabella
    L.point("N2", (0.0, -0.06))   # nose tip
    L.point("SN", (0.0, -0.2))    # subnasale (columella base)
    L.point("C", (0.0, -1.0))     # chin tip
    L.point("C2", (0.0, -1.4))    # under the chin, where it meets the neck
    # Nose
    L.point("P", (0.045, -0.07))  # tip side of the nostril area
    L.point("R", (0.06, -0.205))  # columella base, beside the subnasale
    L.point("X", (0.25, -0.2))    # alar base (top of the nasolabial fold)
    L.point("Y", (0.21, -0.01))   # nose side above the nose wing
    L.point("M1", (0.11, 0.03))   # nose side, between tip and wing
    L.point("M2", (0.15, 0.44))   # brow line, above the nose bridge
    # Mask outline and cheek
    L.point("OT", (0.75, 1.0))
    L.point("OR", (0.92, 0.25))
    L.point("OJ", (0.82, -0.5))
    L.point("OJ2", (0.7, -0.95))
    L.point("K", (0.56, -0.12))
    L.curve("C", "OJ", bezier((0.0, -1.0), (0.6, -0.98), (0.82, -0.5)))
    L.curve("OJ", "OR", bezier((0.82, -0.5), (0.95, -0.1), (0.92, 0.25)))
    L.curve("OR", "OT", bezier((0.92, 0.25), (0.92, 0.75), (0.75, 1.0)))
    L.curve("C2", "OJ2", bezier((0.0, -1.4), (0.5, -1.35), (0.7, -0.95)))

    # Eye loops
    L.patch("eR", "ER", "ET", "eT")
    L.patch("eT", "ET", "EL", "eL")
    L.patch("eL", "EL", "EB", "eB")
    L.patch("eB", "EB", "ER", "eR")
    # Mouth loops
    L.patch("mT", "MT", "MU", "mU")
    L.patch("mU", "MU", "MR", "mR")
    L.patch("mR", "MR", "MB", "mB")
    # Nose: columella strip, nostril (rim + alar loops) inside a pentagon
    L.patch("N2", "SN", "R", "P")
    _ring(L, NOSTRIL_C, ["P", "R", "X", "Y", "M1"], NOSTRIL_IN, NOSTRIL_OUT, "h", "r")
    # Upper lip
    L.patch("SN", "MT", "MU", "R")
    L.patch("R", "MU", "MR", "X")
    # Pentagons (one valence-5 pole each)
    L.npatch("N1", "N2", "P", "M1", "M2")     # nose bridge
    L.npatch("M1", "Y", "EL", "ET", "M2")     # nose side / inner eye
    L.npatch("F", "N1", "M2", "ET", "OT")     # forehead
    L.npatch("X", "K", "EB", "EL", "Y")       # under the eye
    L.npatch("X", "MR", "OJ", "OR", "K")      # cheek / nasolabial
    # Quads
    L.patch("OT", "ET", "ER", "OR")           # brow / temple
    L.patch("EB", "K", "OR", "ER")            # cheek bone
    L.patch("MR", "MB", "C", "OJ")            # chin / jaw
    L.patch("C", "C2", "OJ2", "OJ")           # under the chin
    if head:
        _head(L)
    return L


def counts(density=1):
    """Segment counts per chord. ``density`` scales everything."""
    d = max(1, int(density))
    a = d + 1                      # half of the pentagon side count
    return {
        ("N1", "N2"): 2 * a,      # pentagon chord: nose, forehead, cheeks...
        ("eR", "ER"): 4 * d,      # loops around the eye
        ("mT", "MT"): 4 * d,      # loops around the mouth
        ("P", "rP"): 2 * d,       # alar loops around the nostril
        ("rP", "hP"): d,          # nostril rim loops
        ("ER", "ET"): 2 * a,      # eye, outer-top quarter
        ("MR", "MU"): a,          # upper lip, outer part
        ("MR", "MB"): 3 * a,      # lower lip / chin columns (= upper lip)
        ("N2", "SN"): d + 1,      # columella height
        ("C", "C2"): 2 * d,       # under the chin
        # head: loops around the face (inner / ear / outer levels)
        ("F", "F>CR1"): 2 * d,
        ("F>CR2", "F>CR3"): 2 * d + 1,
        ("F>CR3", "CR"): 3 * d,
    }


def _nose(u, v):
    """Nose relief: a ridge rising from the glabella to the tip, then
    falling steeply to the subnasale, widening towards the nose wings, so
    the nostrils of the template sit on the underside of the nose."""
    top, tip, base = 0.45, -0.05, -0.27
    h = np.where(v > tip, 0.32 * np.clip((top - v) / (top - tip), 0, 1),
                 0.32 * np.clip((v - base) / (tip - base), 0, 1) ** 0.6)
    width = 0.07 + 0.15 * np.clip((top - v) / (top - base), 0, 1)
    across = np.clip(1.0 - (u / width) ** 2, 0, 1) ** 1.5
    z = h * across
    # nose wings around the nostrils
    nx, ny = NOSTRIL_C
    z += 0.08 * np.exp(-(((np.abs(u) - nx) / 0.08) ** 2 + ((v - ny) / 0.07) ** 2))
    return z


def canonical(u, v):
    """Rough 3D face (x right, y up, z forward) for the layout point (u, v):
    the mask wraps around the head like a real face and turns under the jaw
    below the chin, so landmark warping starts close to a real scan."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    a = 1.2 * u
    x = np.sin(a) / 1.2
    z = (np.cos(a) - 1.0) / 1.2 * (1.0 - 0.1 * v)
    y = v.copy()
    under = v < -1.0
    t = np.where(under, -1.0 - v, 0.0)
    y = np.where(under, -1.0 - t * 0.35, y)
    z = z - t * 1.6
    z += _nose(u, v)
    z -= 0.10 * np.exp(-(((np.abs(u) - EYE_C[0]) ** 2) / 0.02 + (v - EYE_C[1]) ** 2 / 0.015))
    z += 0.06 * np.exp(-(u ** 2 / 0.06 + (v - MOUTH_C[1]) ** 2 / 0.02))     # lips
    return np.column_stack([x, y, z])


# Landmarks: (id, layout point, paired?, Japanese name, English name)
LANDMARKS = [
    ("top", "F", False, "おでこの上端の中央（髪の生え際 / マスク上端）", "Top of the forehead, centre (hairline)"),
    ("glabella", "N1", False, "眉間（左右の眉頭の間）", "Between the eyebrows"),
    ("nose_tip", "N2", False, "鼻の先（一番前に出た所）", "Nose tip"),
    ("subnasale", "SN", False, "鼻の下（鼻柱と上唇の境目）", "Under the nose (columella base)"),
    ("upper_lip", "mT", False, "唇の合わせ目の中央（上唇側）", "Lip line centre (upper lip side)"),
    ("lower_lip", "mB", False, "唇の合わせ目の中央（下唇側）", "Lip line centre (lower lip side)"),
    ("chin", "C", False, "あご先（下あごの先端）", "Chin tip"),
    ("under_chin", "C2", False, "あごの下（あごと首の境目の中央）", "Under the chin, where it meets the neck"),
    ("eye_inner", "eL", True, "目頭", "Inner eye corner"),
    ("eye_outer", "eR", True, "目尻", "Outer eye corner"),
    ("eye_top", "eT", True, "上まぶたの縁の中央", "Upper eyelid edge, centre"),
    ("eye_bottom", "eB", True, "下まぶたの縁の中央", "Lower eyelid edge, centre"),
    ("nostril_inner", (NOSTRIL_C[0] - NOSTRIL_IN[0], NOSTRIL_C[1]), True,
     "鼻の穴の内側の端（鼻柱側）", "Nostril, inner edge (columella side)"),
    ("nostril_outer", (NOSTRIL_C[0] + NOSTRIL_IN[0], NOSTRIL_C[1]), True,
     "鼻の穴の外側の端（小鼻側）", "Nostril, outer edge (nose wing side)"),
    ("ala", (NOSTRIL_C[0] + NOSTRIL_OUT[0] * 1.45, NOSTRIL_C[1]), True,
     "小鼻の外側（小鼻のふくらみの一番外）", "Outermost point of the nose wing"),
    ("nasolabial", "X", True, "ほうれい線の上端（小鼻の付け根の横）", "Top of the nasolabial fold"),
    ("mouth_corner", "mR", True, "口角", "Mouth corner"),
    ("temple", "OR", True, "こめかみ（目の高さでのマスクの横の端）", "Temple, mask side at eye level"),
    ("jaw", "OJ", True, "エラの角（あごの角）", "Jaw angle"),
    ("under_jaw", "OJ2", True, "エラの下（あご下と首の境目）", "Under the jaw angle, where it meets the neck"),
    ("top_side", "OT", True, "おでこの上端の角（生え際の角）", "Top corner of the forehead"),
]

# Extra guides for the whole head (Coverage: Head)
HEAD_LANDMARKS = [
    ("crown", "CR", False, "頭頂（頭の一番高い所）", "Crown, top of the head"),
    ("back_head", "BH", False, "後頭部（頭の一番後ろに出た所）", "Back of the head"),
    ("nape", "NP", False, "うなじ（後頭部と首の境目の中央）", "Nape, where the head meets the neck"),
    ("neck_back", "NB", False, "首の後ろの下端（作る範囲の下端）", "Bottom of the neck, back"),
    ("neck_front", "NF", False, "首の前の下端（のどの下・作る範囲の下端）", "Bottom of the neck, front"),
    ("ear_top", ("arc", "xOR>NP2", "xOR>NP3"), True, "耳の付け根の上端", "Top of the ear attachment"),
    ("ear_bottom", ("arc", "xOJ>NB2", "xOJ>NB3"), True, "耳の付け根の下端（耳たぶの付け根）",
     "Bottom of the ear attachment (earlobe)"),
    ("neck_side", "NA", True, "首の横の下端", "Bottom of the neck, side"),
]


def landmarks(head=False):
    return LANDMARKS + (HEAD_LANDMARKS if head else [])


# Rough 3D head (canonical space) for the head part of the template.
HEAD_CANON = {
    "CR": (0.0, 1.95, -0.9), "BH": (0.0, 0.75, -2.05), "NP": (0.0, -0.45, -1.8),
    "NB": (0.0, -1.95, -1.5), "NF": (0.0, -2.05, -0.5), "NA": (0.62, -2.0, -1.0),
}
HEAD_CENTRE = np.array([0.0, 0.25, -0.95])


def _head_sdf(P):
    """Signed distance-ish field of a simple head: cranium + jaw + neck."""
    P = np.atleast_2d(P)

    def ell(c, r):
        q = (P - c) / r
        return (np.linalg.norm(q, axis=1) - 1.0) * min(r)

    def capsule(a, b, rad):
        ab = b - a
        t = np.clip(((P - a) @ ab) / (ab @ ab), 0.0, 1.0)
        return np.linalg.norm(P - (a + t[:, None] * ab), axis=1) - rad

    def smin(x, y, k=0.25):
        return -k * np.log(np.exp(-x / k) + np.exp(-y / k))

    d = ell(np.array([0.0, 0.55, -1.0]), np.array([0.85, 1.4, 1.1]))
    d = smin(d, ell(np.array([0.0, -0.45, -0.7]), np.array([0.72, 0.75, 0.75])))
    d = smin(d, capsule(np.array([0.0, 0.2, -1.0]), np.array([0.0, -2.8, -1.0]), 0.55))
    return d


def _project_radial(P):
    """Move points along the ray from the head centre onto the head surface."""
    P = np.atleast_2d(P)
    D = P - HEAD_CENTRE
    D /= np.maximum(np.linalg.norm(D, axis=1), 1e-12)[:, None]
    lo = np.zeros(len(P))
    hi = np.full(len(P), 5.0)
    for _ in range(40):
        mid = (lo + hi) * 0.5
        inside = _head_sdf(HEAD_CENTRE + D * mid[:, None]) < 0
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    return HEAD_CENTRE + D * ((lo + hi) * 0.5)[:, None]


def _slerp_dir(a, b, t):
    """Point between a and b going around the head centre."""
    da, db = a - HEAD_CENTRE, b - HEAD_CENTRE
    ra, rb = np.linalg.norm(da), np.linalg.norm(db)
    ua, ub = da / ra, db / rb
    om = math.acos(float(np.clip(ua @ ub, -1, 1)))
    if om < 1e-6:
        u = ua
    else:
        u = (math.sin((1 - t) * om) * ua + math.sin(t * om) * ub) / math.sin(om)
    return HEAD_CENTRE + u * (ra * (1 - t) + rb * t)


def _corner_quality(uv, f):
    """Smallest normalised corner cross product of a quad (< 0: folded)."""
    q = 1.0
    for k in range(4):
        a, b, c = uv[f[k - 1]], uv[f[k]], uv[f[(k + 1) % 4]]
        e0, e1 = b - a, c - b
        den = np.linalg.norm(e0) * np.linalg.norm(e1)
        if den < 1e-12:
            return -1.0
        q = min(q, (e0[0] * e1[1] - e0[1] * e1[0]) / den)
    return q


def _relax2d(uv, faces, fixed, iters=30, lam=0.5):
    """Smart Laplacian smoothing of the half layout: each vertex moves to
    the average of its neighbours only if that does not make the worst
    corner of its faces worse, so folds get untangled and none are created.
    The layout boundary (outline, centre line, openings) stays put."""
    n = len(uv)
    nbrs = [set() for _ in range(n)]
    vfaces = [[] for _ in range(n)]
    edge_count = {}
    for fi, f in enumerate(faces):
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            nbrs[a].add(b)
            nbrs[b].add(a)
            vfaces[a].append(fi)
            key = (min(a, b), max(a, b))
            edge_count[key] = edge_count.get(key, 0) + 1
    boundary = {v for e, c in edge_count.items() if c == 1 for v in e}
    movable = [i for i in range(n) if i not in fixed and i not in boundary]
    uv = uv.copy()
    nb = [list(s) for s in nbrs]
    for _ in range(iters):
        for i in movable:
            old = uv[i].copy()
            before = min(_corner_quality(uv, faces[fi]) for fi in vfaces[i])
            uv[i] = old + lam * (uv[nb[i]].mean(axis=0) - old)
            after = min(_corner_quality(uv, faces[fi]) for fi in vfaces[i])
            if after < min(before, 0.05):
                uv[i] = old
    return uv


def _vertex_of(p, uv, pts, L=None):
    """Template vertex of a landmark: a layout point name, ("arc", a, b) for
    the middle of the curve a-b, or the vertex nearest to a (u, v) position."""
    if isinstance(p, str):
        return pts[p]
    if isinstance(p, tuple) and p and p[0] == "arc":
        p = L._curve(p[1], p[2])(0.5)
    return int(np.argmin(np.linalg.norm(uv - np.asarray(p), axis=1)))


class FaceTemplate:
    """Full (mirrored) face / head template."""

    def __init__(self, density=1, head=False):
        L = _layout(head)
        uv, faces, pts = L.build(counts(density))
        tags = list(L.face_tags)
        self.head = head
        lms = landmarks(head)
        lm_vid = {lid: _vertex_of(p, uv, pts, L) for lid, p, *_r in lms}
        # Only the layout boundary (outline, centre line, openings) stays
        # put; every inner vertex evens out.
        uv = _relax2d(uv, faces, fixed=set(),
                      iters=8 * max(1, int(density)))
        n = len(uv)
        center = np.abs(uv[:, 0]) < 1e-9
        uv[center, 0] = 0.0
        half_co = canonical(uv[:, 0], uv[:, 1])
        head_only = np.zeros(n, dtype=bool)
        ear_only = np.zeros(n, dtype=bool)
        if head:
            half_co = _head_canonical(L, pts, uv, faces, tags, half_co, center)
            hv = {v for f, t in zip(faces, tags) if t in ("head", "ear") for v in f}
            fv = {v for f, t in zip(faces, tags) if t not in ("head", "ear") for v in f}
            head_only[list(hv - fv)] = True
            ear_v = {v for f, t in zip(faces, tags) if t == "ear" for v in f}
            ear_only[list(ear_v)] = True
        mirror = np.arange(n)
        extra = []
        for i in range(n):
            if not center[i]:
                mirror[i] = n + len(extra)
                extra.append(i)
        full_uv = np.concatenate([uv, uv[extra] * np.array([-1.0, 1.0])]) if extra else uv
        full_mirror = np.concatenate([mirror, np.array(extra, dtype=int)])
        mfaces = [tuple(int(mirror[i]) for i in reversed(f)) for f in faces]
        self.uv = full_uv
        self.faces = list(faces) + mfaces
        self.mirror = full_mirror          # vertex -> mirrored vertex
        self.head_mask = np.concatenate([head_only, head_only[extra]]) if extra else head_only
        self.ear_mask = np.concatenate([ear_only, ear_only[extra]]) if extra else ear_only
        self.co = np.concatenate([half_co, half_co[extra] * np.array([-1.0, 1.0, 1.0])]) \
            if extra else half_co
        # Landmark vertex indices: right side (u > 0) and mirrored side.
        self.landmarks = []
        for lid, p, paired, ja, en in lms:
            i = lm_vid[lid]
            self.landmarks.append((lid, i, ja, en, False))
            if paired:
                self.landmarks.append((lid + "_m", int(mirror[i]), ja, en, True))


def _head_canonical(L, pts, uv, faces, tags, co, center):
    """3D positions of the head part: controls (mask outline from the face
    canonical, hand-placed head points, spoke points going round the head)
    are interpolated, projected onto a simple head shape and relaxed."""
    from . import fit
    head_v = {v for f, t in zip(faces, tags) if t in ("head", "ear") for v in f}
    face_v = {v for f, t in zip(faces, tags) if t not in ("head", "ear") for v in f}
    outline = sorted(head_v & face_v)
    only_head = sorted(head_v - face_v)
    src, dst = [], []
    for v in outline:
        src.append(uv[v])
        dst.append(co[v])
    canon = {k: np.array(v) for k, v in HEAD_CANON.items()}
    for name, p3 in canon.items():
        src.append(L.points[name])
        dst.append(p3)
    # Spoke level points go round the head between their two ends.
    face_end = {name: co[pts[name]] for name in ("F", "OT", "OR", "OJ", "OJ2", "C2")}
    ends = {**face_end, **canon}
    for name in L.points:
        if ">" in name and name[0] not in "xy":
            a, rest = name.split(">")
            b, k = rest[:-1], int(rest[-1])
            t = HEAD_LEVELS[k - 1]
            src.append(L.points[name])
            dst.append(_slerp_dir(ends[a], ends[b], t))
    # Neck bottom: an ellipse around the neck (back -> side -> front).
    neck_fixed = {}
    for (p, q), (th0, th1) in ((("NB", "NA"), (math.pi, math.pi / 2)),
                               (("NA", "NF"), (math.pi / 2, 0.0))):
        fn = L._curve(p, q)
        ts = np.linspace(0.0, 1.0, 801)
        samples = np.array([fn(t) for t in ts])
        for v in only_head:
            dd = np.linalg.norm(samples - uv[v], axis=1)
            k = int(np.argmin(dd))
            if dd[k] < 1e-4:
                th = th0 + (th1 - th0) * ts[k]
                neck_fixed[v] = np.array([0.6 * math.sin(th), -2.0, -1.0 + 0.5 * math.cos(th)])
    for v, p3 in neck_fixed.items():
        src.append(uv[v])
        dst.append(p3)
    src = np.array([[p[0], p[1], 0.0] for p in src])
    dst = np.array(dst)
    P = np.array([[uv[v][0], uv[v][1], 0.0] for v in only_head])
    W = fit.rbf_warp(P, src, dst)
    # The face mask sticks out of the simple head shape a little; carry the
    # ratio (mask distance / head-shape distance from the centre) at the
    # outline smoothly into the head so there is no step at the mask edge.
    o_co = co[outline]
    o_ratio = (np.linalg.norm(o_co - HEAD_CENTRE, axis=1)
               / np.linalg.norm(_project_radial(o_co) - HEAD_CENTRE, axis=1))
    r_src = [[uv[v][0], uv[v][1], 0.0] for v in outline]
    r_val = list(o_ratio)
    for name in HEAD_CANON:
        r_src.append([L.points[name][0], L.points[name][1], 0.0])
        r_val.append(1.0)
    r_src = np.array(r_src)
    r_dst = np.column_stack([np.array(r_val), np.zeros(len(r_val)), np.zeros(len(r_val))])
    ratio_at = lambda Q: fit.rbf_warp(Q, r_src, r_dst + r_src)[:, 0] - Q[:, 0]
    ratio = ratio_at(P)

    def on_head(X):
        Y = _project_radial(X)
        return HEAD_CENTRE + (Y - HEAD_CENTRE) * ratio[:, None]

    co = co.copy()
    co[only_head] = on_head(W)
    for v, p3 in neck_fixed.items():
        co[v] = p3
    cmask = np.array([center[v] for v in only_head])
    co[np.array(only_head)[cmask], 0] = 0.0
    # Even the spacing on the head shape. Mask outline, neck bottom and ear
    # opening stay; centre-line vertices stay on the mirror plane.
    count = {}
    for f in faces:
        for k in range(4):
            e = (min(f[k], f[(k + 1) % 4]), max(f[k], f[(k + 1) % 4]))
            count[e] = count.get(e, 0) + 1
    border = {v for e, c in count.items() if c == 1 for v in e}
    free = np.array([v not in border or center[v] for v in only_head])
    idx = np.array(only_head)[free]
    cidx = np.array([v for v in idx if center[v]], dtype=int)
    adj = fit.Adjacency(len(co), faces)
    for _ in range(20):
        avg = adj.average(co)
        co[idx] = co[idx] + 0.5 * (avg[idx] - co[idx])
        if len(cidx):
            co[cidx, 0] = 0.0
        sub = np.isin(np.array(only_head), idx)
        Y = on_head(co[only_head])
        co[np.array(only_head)[sub]] = Y[sub]
        if len(cidx):
            co[cidx, 0] = 0.0
    for v, p3 in neck_fixed.items():
        co[v] = p3
    return co


def _smooth_socket(V, faces, ear_mask, pinned, iters=25):
    from . import fit
    adj = fit.Adjacency(len(V), faces)
    edges = {}
    for f in faces:
        for k in range(4):
            e = (min(f[k], f[(k + 1) % 4]), max(f[k], f[(k + 1) % 4]))
            edges[e] = edges.get(e, 0) + 1
    # Socket vertices that are not on its outer border with the scalp.
    outer = set()
    for f in faces:
        if not all(ear_mask[v] for v in f):
            outer.update(v for v in f if ear_mask[v])
    hole_nb = {}
    for (a, b), c in edges.items():
        if c == 1 and ear_mask[a] and ear_mask[b]:
            hole_nb.setdefault(a, []).append(b)
            hole_nb.setdefault(b, []).append(a)
    inner = [v for v in np.nonzero(ear_mask)[0] if v not in outer and v not in hole_nb]
    hole = [v for v, n in hole_nb.items() if len(n) == 2 and v not in pinned]
    V = V.copy()
    for _ in range(iters):
        avg = adj.average(V)
        V[inner] = V[inner] + 0.5 * (avg[inner] - V[inner])
        for v in hole:
            a, b = hole_nb[v]
            V[v] = V[v] + 0.5 * ((V[a] + V[b]) * 0.5 - V[v])
    return V


CENTER_IDS = [lid for lid, _p, paired, *_r in LANDMARKS + HEAD_LANDMARKS if not paired]


def build_face(guides, nearest, density=1, symmetric=True, iters=40, samples=None, ray=None,
               head=False, normal=None):
    """Fit the face template to a scan.

    ``guides`` maps landmark id -> 3D point (scan space). In symmetric mode
    only one side of the paired landmarks is needed; the other side is
    mirrored across the plane through the centre landmarks.
    Returns (verts (N, 3), faces)."""
    from . import fit
    T = FaceTemplate(density, head)
    guides = {k: np.asarray(v, float) for k, v in guides.items()}
    plane = None
    mirror = None
    if symmetric:
        centre = [guides[k] for k in CENTER_IDS if k in guides]
        if len(centre) >= 3:
            plane = fit.mirror_plane(centre)
            if samples is not None:
                # Scan points around the face drive the symmetry refinement.
                pts = np.array(list(guides.values()))
                mid = pts.mean(0)
                rad = np.linalg.norm(pts - mid, axis=1).max() * 1.1
                S = np.asarray(samples, float)
                S = S[np.linalg.norm(S - mid, axis=1) < rad]
                if len(S) > 3000:
                    S = S[np.linspace(0, len(S) - 1, 3000).astype(int)]
                plane = fit.refine_mirror_plane(plane, S, nearest)
            mirror = T.mirror
            for lid, _p, paired, *_r in landmarks(head):
                if paired and lid in guides and lid + "_m" not in guides:
                    guides[lid + "_m"] = nearest(fit.reflect(guides[lid][None], plane))[0]
    idx, tgt = [], []
    for lid, vi, *_rest in T.landmarks:
        if lid in guides:
            idx.append(vi)
            tgt.append(guides[lid])
    if len(idx) < 4:
        raise ValueError("at least 4 guide points are needed")
    inside = None
    need = ["ear_top", "ear_bottom", "ear_top_m", "ear_bottom_m", "neck_front", "neck_back",
            "neck_side", "neck_side_m", "crown"]
    if head and ray is not None and all(k in guides for k in need):
        # Head part: snap from the head / neck axis outwards.
        ears = np.mean([guides[k] for k in need[:4]], axis=0)
        neck = np.mean([guides[k] for k in need[4:8]], axis=0)
        top = ears + (guides["crown"] - ears) * 0.3
        inside = ((top, neck), T.head_mask)
    V, F = fit.fit_template(T.co, T.faces, np.array(idx), np.array(tgt), nearest,
                            iters=iters, mirror=mirror, plane=plane, ray=ray, normal=normal,
                            inside=inside)
    one = lambda p: nearest(np.asarray(p)[None])[0]
    if head:
        # The bottom of the neck: an even, smooth border.
        V = fit.smooth_border(V, F, T.head_mask & (T.co[:, 1] < -1.9), one)

    # Final light smoothing on the surface evens out stretched quads.
    V = fit.relax_on_surface(V, F, one, 6, 0.3)
    V[idx] = tgt
    if head:
        # Ear socket: a clean ring around the ear base to attach an ear to
        # (the ear itself is not retopologised): smoothed, not snapped into
        # the folds of the ear; the outer ring of the socket stays put.
        V = _smooth_socket(V, F, T.ear_mask, set(idx))
    return V, F
