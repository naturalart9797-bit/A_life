# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural face-mask template with production-style topology.

Half face (u >= 0, u = 0 is the centre line) laid out as quad patches:
concentric loops around the eye opening and around the mouth opening,
a nasolabial pole beside the nose and poles at the inner eye corner and
mouth corner -- the usual layout of a hand-made face retopology. The half
is mirrored to a full mask.

Canonical space: u right, v up (chin v = -1, top of mask v = 1), half width
about 1. ``depth`` gives a rough 3D face so landmark warping starts from a
face-like shape.
"""

import math

import numpy as np

from .patches import Layout, bezier, ellipse_arc

EYE_C = (0.38, 0.22)
EYE_IN = (0.12, 0.05)    # eye opening radii
EYE_OUT = (0.22, 0.17)   # outer edge of the eye loops
MOUTH_C = (0.0, -0.45)
MOUTH_IN = (0.20, 0.035)
MOUTH_OUT = (0.32, 0.19)

H = math.pi / 2


def _layout():
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
    # Mouth (half): T / B on the centre line, R = mouth corner.
    for pre, (rx, ry) in (("m", MOUTH_IN), ("M", MOUTH_OUT)):
        L.point(pre + "T", (mx, my + ry))
        L.point(pre + "R", (mx + rx, my))
        L.point(pre + "B", (mx, my - ry))
        L.curve(pre + "R", pre + "T", ellipse_arc(MOUTH_C, rx, ry, 0, H))
        L.curve(pre + "B", pre + "R", ellipse_arc(MOUTH_C, rx, ry, -H, 0))

    L.point("F", (0.0, 1.0))      # top of mask, centre
    L.point("N1", (0.0, 0.45))    # glabella
    L.point("N2", (0.0, -0.1))   # nose tip
    L.point("C", (0.0, -1.0))     # chin
    L.point("OT", (0.75, 1.0))    # top side corner of the mask
    L.point("OR", (0.92, 0.25))   # temple (mask side at eye level)
    L.point("OJ", (0.82, -0.5))   # jaw angle (mask side at mouth level)
    L.point("K", (0.55, -0.15))   # cheek
    L.point("Q", (0.27, -0.2))   # nasolabial pole beside the nose wing
    L.curve("C", "OJ", bezier((0.0, -1.0), (0.6, -0.98), (0.82, -0.5)))
    L.curve("OJ", "OR", bezier((0.82, -0.5), (0.95, -0.1), (0.92, 0.25)))
    L.curve("OR", "OT", bezier((0.92, 0.25), (0.92, 0.75), (0.75, 1.0)))

    # Eye loops
    L.patch("eR", "ER", "ET", "eT")
    L.patch("eT", "ET", "EL", "eL")
    L.patch("eL", "EL", "EB", "eB")
    L.patch("eB", "EB", "ER", "eR")
    # Mouth loops
    L.patch("mT", "MT", "MR", "mR")
    L.patch("mR", "MR", "MB", "mB")
    # Rest of the face
    L.patch("F", "N1", "ET", "OT")     # forehead
    L.patch("OT", "ET", "ER", "OR")    # brow / temple
    L.patch("N1", "N2", "EL", "ET")    # nose bridge
    L.patch("N2", "MT", "Q", "EL")     # nose side
    L.patch("EL", "Q", "K", "EB")      # under the eye (inner)
    L.patch("EB", "K", "OR", "ER")     # cheek bone
    L.patch("MT", "MR", "K", "Q")      # nasolabial
    L.patch("MR", "OJ", "OR", "K")     # cheek
    L.patch("MR", "MB", "C", "OJ")     # chin / jaw
    return L


def counts(density=1):
    """Segment counts per chord. ``density`` multiplies the base counts."""
    d = max(1, int(density))
    return {
        ("eR", "ER"): 2 * d,      # loops around the eye
        ("mT", "MT"): 2 * d,      # loops around the mouth
        ("ER", "ET"): 3 * d,      # eye quarter (outer-top)
        ("ET", "EL"): 3 * d,      # eye quarter (inner-top) / nose bridge
        ("EL", "EB"): 3 * d,      # eye quarter (inner-bottom) / upper lip
        ("EB", "ER"): 3 * d,      # eye quarter (outer-bottom) / cheek
        ("MR", "MB"): 3 * d,      # lower lip
        ("N1", "ET"): 2 * d,      # horizontal chord: brow, nose side, cheek
        ("F", "N1"): 3 * d,       # vertical chord: forehead, nose length
    }


def depth(u, v):
    """Rough face relief (z forward) used for the canonical 3D template."""
    u = np.asarray(u, dtype=float)
    v = np.asarray(v, dtype=float)
    z = 0.9 * np.sqrt(np.clip(1.0 - (u / 1.2) ** 2, 0.0, None)) * (1.0 - 0.12 * v ** 2)
    z += 0.28 * np.exp(-(u ** 2 / 0.010 + (v + 0.02) ** 2 / 0.05))          # nose
    z -= 0.08 * np.exp(-(((np.abs(u) - EYE_C[0]) ** 2) / 0.02 + (v - EYE_C[1]) ** 2 / 0.015))
    z += 0.05 * np.exp(-(u ** 2 / 0.06 + (v - MOUTH_C[1]) ** 2 / 0.02))     # lips
    return z


# Landmarks: (id, layout point, paired?, Japanese name, English name)
LANDMARKS = [
    ("top", "F", False, "おでこの上（マスク上端の中央）", "Top of mask, centre"),
    ("glabella", "N1", False, "眉間", "Between the eyebrows"),
    ("nose_tip", "N2", False, "鼻の先", "Nose tip"),
    ("subnasale", "MT", False, "鼻の下（鼻と上唇の境目）", "Under the nose"),
    ("upper_lip", "mT", False, "上唇の合わせ目の中央", "Lip line centre (upper)"),
    ("lower_lip", "mB", False, "下唇の合わせ目の中央", "Lip line centre (lower)"),
    ("chin", "C", False, "あご先（マスク下端の中央）", "Chin, bottom of mask"),
    ("eye_inner", "eL", True, "目頭", "Inner eye corner"),
    ("eye_outer", "eR", True, "目尻", "Outer eye corner"),
    ("eye_top", "eT", True, "上まぶたの中央", "Upper eyelid centre"),
    ("eye_bottom", "eB", True, "下まぶたの中央", "Lower eyelid centre"),
    ("mouth_corner", "mR", True, "口角", "Mouth corner"),
    ("temple", "OR", True, "こめかみ（目の高さのマスク端）", "Temple, mask edge at eye level"),
    ("jaw", "OJ", True, "エラ（口の高さのマスク端）", "Jaw angle, mask edge at mouth level"),
    ("top_side", "OT", True, "おでこの上の端（マスク上端の角）", "Top corner of mask"),
]


def _relax2d(uv, faces, fixed, iters=30, lam=0.5):
    """Smooth interior vertices of the half layout (boundary, centre line and
    layout corners stay) so Coons-filled patches become even and convex."""
    n = len(uv)
    nbrs = [set() for _ in range(n)]
    edge_count = {}
    for f in faces:
        for k in range(4):
            a, b = f[k], f[(k + 1) % 4]
            nbrs[a].add(b)
            nbrs[b].add(a)
            key = (min(a, b), max(a, b))
            edge_count[key] = edge_count.get(key, 0) + 1
    boundary = {v for e, c in edge_count.items() if c == 1 for v in e}
    movable = [i for i in range(n) if i not in fixed and i not in boundary]
    uv = uv.copy()
    for _ in range(iters):
        new = uv.copy()
        for i in movable:
            avg = uv[list(nbrs[i])].mean(axis=0)
            new[i] = uv[i] + lam * (avg - uv[i])
        uv = new
    return uv


class FaceTemplate:
    """Full (mirrored) face template."""

    def __init__(self, density=1):
        uv, faces, pts = _layout().build(counts(density))
        uv = _relax2d(uv, faces, fixed={pts[p] for _l, p, *_r in LANDMARKS},
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
        self.co = np.column_stack([full_uv[:, 0], full_uv[:, 1],
                                   depth(full_uv[:, 0], full_uv[:, 1])])
        # Landmark vertex indices: right side (u > 0) and mirrored side.
        self.landmarks = []
        for lid, p, paired, ja, en in LANDMARKS:
            i = pts[p]
            self.landmarks.append((lid, i, ja, en, False))
            if paired:
                self.landmarks.append((lid + "_m", int(mirror[i]), ja, en, True))


CENTER_IDS = [lid for lid, _p, paired, *_r in LANDMARKS if not paired]


def build_face(guides, nearest, density=1, symmetric=True, iters=40):
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
            mirror = T.mirror
            for lid, _p, paired, *_r in LANDMARKS:
                if paired and lid in guides and lid + "_m" not in guides:
                    guides[lid + "_m"] = fit.reflect(guides[lid][None], plane)[0]
    idx, tgt = [], []
    for lid, vi, *_rest in T.landmarks:
        if lid in guides:
            idx.append(vi)
            tgt.append(guides[lid])
    if len(idx) < 4:
        raise ValueError("at least 4 guide points are needed")
    return fit.fit_template(T.co, T.faces, np.array(idx), np.array(tgt), nearest,
                            iters=iters, mirror=mirror, plane=plane)
