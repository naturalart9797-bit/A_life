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


def _layout():
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
    return L


def counts(density=1):
    """Segment counts per chord. ``density`` scales everything."""
    d = max(1, int(density))
    a = d + 1                      # half of the pentagon side count
    return {
        ("N1", "N2"): 2 * a,      # pentagon chord: nose, forehead, cheeks...
        ("eR", "ER"): 3 * d,      # loops around the eye
        ("mT", "MT"): 3 * d,      # loops around the mouth
        ("P", "rP"): 2 * d,       # alar loops around the nostril
        ("rP", "hP"): d,          # nostril rim loops
        ("ER", "ET"): 2 * a,      # eye, outer-top quarter
        ("MR", "MU"): a,          # upper lip, outer part
        ("MR", "MB"): 3 * a,      # lower lip / chin columns (= upper lip)
        ("N2", "SN"): d + 1,      # columella height
        ("C", "C2"): 2 * d,       # under the chin
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
    ("top", "F", False, "おでこの上（マスク上端の中央）", "Top of mask, centre"),
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
    ("top_side", "OT", True, "おでこの上の端（マスク上端の角）", "Top corner of mask"),
]


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


def _vertex_of(p, uv, pts):
    """Template vertex of a landmark: a layout point name or the vertex
    nearest to a (u, v) position."""
    if isinstance(p, str):
        return pts[p]
    return int(np.argmin(np.linalg.norm(uv - np.asarray(p), axis=1)))


class FaceTemplate:
    """Full (mirrored) face template."""

    def __init__(self, density=1):
        uv, faces, pts = _layout().build(counts(density))
        lm_vid = {lid: _vertex_of(p, uv, pts) for lid, p, *_r in LANDMARKS}
        # Only the layout boundary (outline, centre line, eye / mouth /
        # nostril openings) stays put; every inner vertex evens out.
        uv = _relax2d(uv, faces, fixed=set(),
                      iters=8 * max(1, int(density)))
        n = len(uv)
        center = np.abs(uv[:, 0]) < 1e-9
        uv[center, 0] = 0.0
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
        self.co = canonical(full_uv[:, 0], full_uv[:, 1])
        # Landmark vertex indices: right side (u > 0) and mirrored side.
        self.landmarks = []
        for lid, p, paired, ja, en in LANDMARKS:
            i = lm_vid[lid]
            self.landmarks.append((lid, i, ja, en, False))
            if paired:
                self.landmarks.append((lid + "_m", int(mirror[i]), ja, en, True))


CENTER_IDS = [lid for lid, _p, paired, *_r in LANDMARKS if not paired]


def build_face(guides, nearest, density=1, symmetric=True, iters=40, samples=None, ray=None):
    """Fit the face template to a scan.

    ``guides`` maps landmark id -> 3D point (scan space). In symmetric mode
    only one side of the paired landmarks is needed; the other side is
    mirrored across the plane through the centre landmarks.
    Returns (verts (N, 3), faces)."""
    from . import fit
    T = FaceTemplate(density)
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
            for lid, _p, paired, *_r in LANDMARKS:
                if paired and lid in guides and lid + "_m" not in guides:
                    guides[lid + "_m"] = nearest(fit.reflect(guides[lid][None], plane))[0]
    idx, tgt = [], []
    for lid, vi, *_rest in T.landmarks:
        if lid in guides:
            idx.append(vi)
            tgt.append(guides[lid])
    if len(idx) < 4:
        raise ValueError("at least 4 guide points are needed")
    V, F = fit.fit_template(T.co, T.faces, np.array(idx), np.array(tgt), nearest,
                            iters=iters, mirror=mirror, plane=plane, ray=ray)
    # Final light smoothing on the surface evens out stretched quads.
    V = fit.relax_on_surface(V, F, lambda p: nearest(np.asarray(p)[None])[0], 6, 0.3)
    V[idx] = tgt
    return V, F
