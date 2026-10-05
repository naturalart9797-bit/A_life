"""
Diptera (true fly) wing generator.

Unlike dragonflies, fly wings are not covered by a dense network of
cross veins grown by local inhibition.  They carry a small, fixed set of
homologous veins and cells that is conserved across a family and is
used for identification.  So this module builds the wing from the
vein topology (Comstock-Needham system, modern dipteran terminology)
instead of the inhibition / Voronoi model used for Odonata.

Longitudinal veins
    C      costa: thick leading-edge vein.  In Tabanidae it runs around the
           whole wing (circumambient), thinner on the posterior margin.
    Sc     subcosta: ends on C around mid wing.
    R1     first branch of the radius: strong, ends on C.
    Rs     radial sector, splits into R2+3 and R4+5.
           Tabanidae: R4+5 forks again into R4 and R5 around the apex
           (R4 often with a short appendix).
    M      media: M1, M2 (Tabanidae) or one bent M1 (Muscidae).
    CuA    anterior cubitus (CuA1 to the margin, CuA2 closes cell cup).
    CuP    posterior cubitus: weak fold-like vein.
    A1     first anal vein; joins CuA2 and closes cell cup.

Cross veins
    h      humeral cross vein (C - Sc)
    r-m    radial-medial cross vein
    bm-cu  closes basal medial cell bm
    dm-cu  closes discal cell dm (the "posterior cross vein")

Basal structures
    alula (with alular incision) and calypters (squamae).

Diptera have only one pair of functional wings; the hind wings are
reduced to halteres (built by builder.py).
"""

import math
import random

try:
    from . import venation as _v
except ImportError:  # used outside Blender (tools/preview_svg.py)
    import venation as _v


# ---------------------------------------------------------------------------
# Wing shape with alula lobe
# ---------------------------------------------------------------------------

class FlyParams:
    def __init__(self, **kw):
        self.family = "TABANIDAE"
        self.length = 1.0
        self.chord = 0.36
        self.base_power = 0.55
        self.tip_power = 0.55
        self.tip_drop = 0.22
        self.le_bulge = 0.03
        self.alula = 0.10          # alula lobe size (x chord)
        self.alula_pos = 0.13      # x/L of the alula
        self.calypter = 0.10       # calypter radius (x chord), 0 = none
        self.r4_appendix = True    # short spur on R4 (Tabanidae)
        self.variation = 0.3       # random displacement of vein nodes
        self.pigment = 0.8         # tint strength of costal / basal cells
        self.seed = 1
        for k, val in kw.items():
            if not hasattr(self, k):
                raise AttributeError("Unknown fly wing parameter: %s" % k)
            setattr(self, k, val)


class FlyShape(_v.WingShape):
    """Wing outline = core blade + alula lobe separated by the alular
    incision.  Vein coordinates use the core blade only."""

    def __init__(self, p):
        _v.WingShape.__init__(self, p)

    def core_width(self, u):
        # the blade narrows into a short stalk at the wing hinge
        stalk = 1.0 - 0.8 * math.exp(-max(u, 0.0) / 0.03)
        return _v.WingShape.width(self, u) * stalk

    def width(self, u):
        p = self.p
        w = self.core_width(u)
        if p.alula > 0:
            ua = p.alula_pos
            sw = 0.04
            lobe = p.alula * p.chord * math.exp(-((u - ua) / sw) ** 2)
            # alular incision: a narrow notch at the distal end of the lobe
            notch = 0.75 * p.alula * p.chord * math.exp(-((u - ua - 1.6 * sw) / 0.01) ** 2)
            w = max(w * 0.15, w + lobe - notch)
        return w

    def point(self, u, v):
        """Interior point.  u = fraction of the span, v = distance behind the
        (straight) leading edge in units of the chord.  The point is kept
        inside the blade so templates survive changes of the outline."""
        p = self.p
        c = p.chord
        y = c * p.le_bulge * math.sin(math.pi * u) - v * c
        top = self.y_le(u) - 0.012 * c
        bot = self.y_le(u) - self.core_width(u) + 0.02 * c
        y = min(top, max(bot, y)) if bot < top else 0.5 * (top + bot)
        return (u * p.length, y)


# ---------------------------------------------------------------------------
# Family templates
#   nodes : name -> (u, v)                 interior points
#           name -> ("LE", u) / ("TE", u)  margin points
#           name -> ("A", d)               margin point near the apex,
#                                          d = arc offset (x L) from the tip,
#                                          negative = anterior side
#   veins : (name, kind, [node, node, ...])
# ---------------------------------------------------------------------------

# Tabanidae (horse flies): measured from a photograph of a Tabanus wing.
TABANIDAE = dict(
    nodes={
        "h_c": ("LE", 0.06), "h_sc": (0.06, 0.08),
        "sc0": (0.008, 0.055), "sc1": (0.24, 0.05), "sc_e": ("LE", 0.46),
        "r0": (0.008, 0.075), "r1": (0.124, 0.07), "rs0": (0.267, 0.085),
        "r1a": (0.536, 0.045), "r1_e": ("LE", 0.79),
        "rf": (0.357, 0.19),
        "r23a": (0.61, 0.157), "r23_e": ("LE", 0.915),
        "rm_r": (0.509, 0.272), "r45f": (0.74, 0.31),
        "r4a": (0.89, 0.25), "r4_e": ("A", -0.004), "r4app": (0.845, 0.19),
        "r5a": (0.79, 0.43), "r5_e": ("TE", 0.83),
        "m0": (0.008, 0.10), "m1": (0.118, 0.234), "bm_e": (0.268, 0.321),
        "dm_a": (0.498, 0.36), "dm_ud": (0.575, 0.395),
        "dm_md": (0.575, 0.475), "dm_ld": (0.56, 0.55),
        "dm_lp": (0.33, 0.43), "dm_l1": (0.43, 0.49),
        "m1a": (0.69, 0.50), "m1_e": ("TE", 0.775),
        "m2a": (0.66, 0.62), "m2_e": ("TE", 0.71),
        "m3a": (0.61, 0.68), "m3_e": ("TE", 0.645),
        "cu0": (0.008, 0.13), "cu1": (0.132, 0.36), "cub": (0.28, 0.465),
        "cu_bm": (0.33, 0.52), "cu_dm": (0.415, 0.59), "cua1a": (0.475, 0.68),
        "cua1_e": ("TE", 0.555),
        "cua2a": (0.34, 0.70), "cupc": (0.36, 0.83), "cup_e": ("TE", 0.37),
        "a0": (0.012, 0.17), "a1": (0.10, 0.60),
        "cup0": (0.01, 0.15), "cup1": (0.18, 0.56), "cup2": (0.27, 0.68),
    },
    veins=[
        ("Sc", "sc", ["sc0", "h_sc", "sc1", "sc_e"]),
        ("h", "cross", ["h_c", "h_sc"]),
        ("R", "r1", ["r0", "r1", "rs0"]),
        ("R1", "r1", ["rs0", "r1a", "r1_e"]),
        ("Rs", "radial", ["rs0", "rf"]),
        ("R2+3", "radial", ["rf", "r23a", "r23_e"]),
        ("R4+5", "radial", ["rf", "rm_r", "r45f"]),
        ("R4", "radial", ["r45f", "r4a", "r4_e"]),
        ("R5", "radial", ["r45f", "r5a", "r5_e"]),
        ("r-m", "cross", ["rm_r", "dm_a"]),
        ("M", "main", ["m0", "m1", "bm_e", "dm_a", "dm_ud"]),
        ("dm cell (distal)", "main", ["dm_ud", "dm_md", "dm_ld"]),
        ("M1", "main", ["dm_ud", "m1a", "m1_e"]),
        ("M2", "main", ["dm_md", "m2a", "m2_e"]),
        ("M3", "main", ["dm_ld", "m3a", "m3_e"]),
        ("dm cell (posterior)", "main", ["bm_e", "dm_lp", "dm_l1", "dm_ld"]),
        ("CuA1", "main", ["cu0", "cu1", "cub", "cu_bm", "cu_dm", "cua1a", "cua1_e"]),
        ("bm-cu", "cross", ["dm_lp", "cu_bm"]),
        ("dm-cu", "cross", ["dm_l1", "cu_dm"]),
        ("CuA2", "main", ["cub", "cua2a", "cupc", "cup_e"]),
        ("CuP", "weak", ["cup0", "cup1", "cup2"]),
        ("A1", "main", ["a0", "a1", "cupc"]),
    ],
    appendix=("R4 appendix", "weak2", ["r4a", "r4app"]),
    costa_end=0.0,        # thick costa to the apex, then a thin circumambient C
    circumambient=True,
)

# Muscidae (house flies): costa ends at M1, M1 bends sharply forward and
# nearly meets R4+5 at the apex, cell cup short, A1 does not reach margin.
MUSCIDAE = dict(
    nodes={
        "h_c": ("LE", 0.09), "h_sc": (0.09, 0.075),
        "sc0": (0.008, 0.05), "sc1": (0.24, 0.045), "sc_e": ("LE", 0.37),
        "r0": (0.008, 0.075), "r1": (0.10, 0.10), "rs0": (0.18, 0.105),
        "r1a": (0.33, 0.05), "r1_e": ("LE", 0.46),
        "rf": (0.25, 0.155),
        "r23a": (0.50, 0.16), "r23b": (0.72, 0.13), "r23_e": ("LE", 0.82),
        "rm_r": (0.42, 0.245), "r45a": (0.75, 0.30), "r45_e": ("A", 0.012),
        "m0": (0.008, 0.11), "m1": (0.16, 0.27), "bm_e": (0.29, 0.35),
        "dm_a": (0.42, 0.38), "m_dc": (0.62, 0.43), "m_bend": (0.765, 0.448),
        "m1b": (0.845, 0.40), "m1_e": ("A", 0.045),
        "cu0": (0.008, 0.14), "cua2s": (0.12, 0.37), "cub": (0.29, 0.55),
        "cu2": (0.45, 0.66), "cu_dm": (0.62, 0.72), "cua1_e": ("TE", 0.80),
        "cua2a": (0.17, 0.53), "cupc": (0.22, 0.61),
        "a0": (0.012, 0.19), "a1": (0.12, 0.54), "a_end": (0.36, 0.80),
        "cup0": (0.01, 0.16), "cup1": (0.15, 0.50),
    },
    veins=[
        ("Sc", "sc", ["sc0", "h_sc", "sc1", "sc_e"]),
        ("h", "cross", ["h_c", "h_sc"]),
        ("R", "r1", ["r0", "r1", "rs0"]),
        ("R1", "r1", ["rs0", "r1a", "r1_e"]),
        ("Rs", "radial", ["rs0", "rf"]),
        ("R2+3", "radial", ["rf", "r23a", "r23b", "r23_e"]),
        ("R4+5", "radial", ["rf", "rm_r", "r45a", "r45_e"]),
        ("r-m", "cross", ["rm_r", "dm_a"]),
        ("M", "main", ["m0", "m1", "bm_e", "dm_a", "m_dc", "m_bend"]),
        ("M1", "main", ["m_bend", "m1b", "m1_e"]),
        ("CuA1", "main", ["cu0", "cua2s", "cub", "cu2", "cu_dm", "cua1_e"]),
        ("bm-cu", "cross", ["bm_e", "cub"]),
        ("dm-cu", "cross", ["m_dc", "cu_dm"]),
        ("CuA2", "main", ["cua2s", "cua2a", "cupc"]),
        ("A1", "main", ["a0", "a1", "cupc", "a_end"]),
        ("CuP", "weak", ["cup0", "cup1"]),
    ],
    costa_end=0.05,       # costa ends where M1 reaches the margin
    circumambient=False,
)

# Syrphidae (hover flies): vena spuria, and M1 / dm-cu / CuA1 form outer
# cross veins running parallel to the margin (a "false margin").
SYRPHIDAE = dict(
    nodes={
        "h_c": ("LE", 0.10), "h_sc": (0.10, 0.08),
        "sc0": (0.008, 0.05), "sc1": (0.27, 0.05), "sc_e": ("LE", 0.45),
        "r0": (0.008, 0.075), "r1": (0.11, 0.10), "rs0": (0.20, 0.11),
        "r1a": (0.42, 0.05), "r1_e": ("LE", 0.57),
        "rf": (0.29, 0.17),
        "r23a": (0.60, 0.16), "r23b": (0.80, 0.10), "r23_e": ("LE", 0.88),
        "rm_r": (0.40, 0.26), "r45a": (0.70, 0.28), "r45j": (0.905, 0.25),
        "r45_e": ("A", -0.006),
        "vs0": (0.25, 0.30), "vs1": (0.47, 0.315), "vs2": (0.68, 0.33),
        "m0": (0.008, 0.11), "m1": (0.17, 0.29), "bm_e": (0.30, 0.37),
        "dm_a": (0.40, 0.38), "dm_ud": (0.70, 0.43),
        "m1a": (0.80, 0.42), "m1b": (0.875, 0.33),
        "dm_md": (0.735, 0.53), "cu_dm": (0.70, 0.64),
        "cu0": (0.008, 0.14), "cu1": (0.15, 0.44), "cub": (0.30, 0.53),
        "cu2": (0.50, 0.60),
        "cua1a": (0.72, 0.74), "cua1_e": ("TE", 0.70),
        "cua2a": (0.40, 0.73), "cupc": (0.48, 0.87), "cup_e": ("TE", 0.50),
        "a0": (0.012, 0.19), "a1": (0.22, 0.72),
        "cup0": (0.01, 0.16), "cup1": (0.28, 0.66),
    },
    veins=[
        ("Sc", "sc", ["sc0", "h_sc", "sc1", "sc_e"]),
        ("h", "cross", ["h_c", "h_sc"]),
        ("R", "r1", ["r0", "r1", "rs0"]),
        ("R1", "r1", ["rs0", "r1a", "r1_e"]),
        ("Rs", "radial", ["rs0", "rf"]),
        ("R2+3", "radial", ["rf", "r23a", "r23b", "r23_e"]),
        ("R4+5", "radial", ["rf", "rm_r", "r45a", "r45j", "r45_e"]),
        ("r-m", "cross", ["rm_r", "dm_a"]),
        ("vena spuria", "weak", ["vs0", "vs1", "vs2"]),
        ("M", "main", ["m0", "m1", "bm_e", "dm_a", "dm_ud"]),
        ("M1", "main", ["dm_ud", "m1a", "m1b", "r45j"]),
        ("dm-cu", "main", ["dm_ud", "dm_md", "cu_dm"]),
        ("CuA1", "main", ["cu0", "cu1", "cub", "cu2", "cu_dm", "cua1a", "cua1_e"]),
        ("bm-cu", "cross", ["bm_e", "cub"]),
        ("CuA2", "main", ["cub", "cua2a", "cupc", "cup_e"]),
        ("A1", "main", ["a0", "a1", "cupc"]),
        ("CuP", "weak", ["cup0", "cup1"]),
    ],
    costa_end=0.0,
    circumambient=False,
)

FAMILIES = {"TABANIDAE": TABANIDAE, "MUSCIDAE": MUSCIDAE, "SYRPHIDAE": SYRPHIDAE}

# shape presets per family (FlyParams values)
PRESETS = {
    "TABANIDAE": dict(chord=0.38, base_power=0.32, tip_power=0.75, tip_drop=0.20,
                      le_bulge=0.0, alula=0.08, alula_pos=0.06, calypter=0.08,
                      r4_appendix=True),
    "MUSCIDAE": dict(chord=0.40, base_power=0.38, tip_power=0.62, tip_drop=0.32,
                     le_bulge=0.02, alula=0.14, alula_pos=0.07, calypter=0.15,
                     r4_appendix=False),
    "SYRPHIDAE": dict(chord=0.36, base_power=0.36, tip_power=0.68, tip_drop=0.22,
                      le_bulge=0.01, alula=0.12, alula_pos=0.07, calypter=0.11,
                      r4_appendix=False),
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _catmull_rom(pts, samples=10):
    """Centripetal-ish Catmull-Rom through all points (keeps end points)."""
    if len(pts) < 3:
        a, b = pts[0], pts[-1]
        n = samples * 2
        return [_v._lerp(a, b, i / n) for i in range(n + 1)]
    ext = [_v._sub(_v._mul(pts[0], 2.0), pts[1])] + list(pts) + \
          [_v._sub(_v._mul(pts[-1], 2.0), pts[-2])]
    out = []
    for i in range(1, len(ext) - 2):
        p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
        for k in range(samples):
            t = k / samples
            t2, t3 = t * t, t * t * t
            out.append(tuple(
                0.5 * (2 * p1[j] + (-p0[j] + p2[j]) * t
                       + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                       + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3)
                for j in range(2)))
    out.append(pts[-1])
    return out


class _Result(_v.Venation):
    def __init__(self):
        _v.Venation.__init__(self)
        self.lobes = []          # calypter polygons
        self.tint = None         # f(x, y) -> 0..1 membrane pigment
        self.labels = {}         # vein name -> Vein


def generate(p):
    tmpl = FAMILIES[p.family]
    rng = random.Random(p.seed)
    shape = FlyShape(p)
    outline, tip_idx = shape.outline(220)
    margin = _v.Margin(outline)
    s_tip = margin.cum[tip_idx]
    L = p.length

    # --- resolve nodes (with shared random displacement) ---
    var = p.variation
    pos = {}
    for name, spec in tmpl["nodes"].items():
        if isinstance(spec[0], str):
            kind, val = spec
            if kind == "A":
                s = s_tip + (val + var * 0.012 * (rng.random() - 0.5)) * L
            else:
                u = val + var * 0.02 * (rng.random() - 0.5)
                s = margin.param_of_x(u * L, kind == "LE")
            pos[name] = margin.point(s)
        else:
            u, v = spec
            u += var * 0.025 * (rng.random() - 0.5)
            v += var * 0.04 * (rng.random() - 0.5)
            pos[name] = shape.point(u, v)

    res = _Result()
    res.shape = shape
    res.outline = outline

    # --- costa and margin ---
    # thick costa from the base to costa_end (arc offset from the apex);
    # beyond it the margin is either a thin vein (circumambient costa,
    # Tabanidae) or only a fine membrane rim.
    costa_s = s_tip + tmpl["costa_end"] * L
    res.veins.append(_v.Vein(margin.sub_polyline(0.0, costa_s), "costa", 0))
    rest = margin.sub_polyline(costa_s, margin.total)
    res.veins.append(_v.Vein(rest, "margin" if tmpl["circumambient"] else "rim", 3))

    # --- veins ---
    veins = list(tmpl["veins"])
    if p.r4_appendix and tmpl.get("appendix"):
        veins.append(tmpl["appendix"])
    for name, kind, nodes in veins:
        pts = _catmull_rom([pos[n] for n in nodes], 12)
        vein = _v.Vein(pts, kind, 1)
        vein.name = name
        res.veins.append(vein)
        res.labels[name] = vein

    # --- calypters (squamae): two stacked flaps at the posterior wing
    # base, next to the body.  The lower one is larger and shields the
    # haltere (well developed in the Calyptratae, e.g. Muscidae).
    if p.calypter > 0:
        r = p.calypter * p.chord
        hy = shape.y_le(0.0) - shape.core_width(0.04) * 0.6
        for cx, cy, rr in ((0.012 * L, hy - 0.95 * r, r),
                           (0.03 * L, hy - 0.55 * r, 0.7 * r)):
            poly = []
            for i in range(32):
                a = 2 * math.pi * i / 32
                poly.append((cx + rr * 0.7 * math.cos(a), cy + rr * math.sin(a)))
            res.lobes.append(poly)

    # --- membrane pigmentation (base and costal cells darker) ---
    def tint(x, y):
        u = min(max(x / L, 0.0), 1.0)
        w = max(shape.core_width(u), 1e-6)
        v = (shape.y_le(u) - y) / w
        costal = math.exp(-max(v, 0.0) / 0.14) * (1.0 - 0.7 * u)
        basal = math.exp(-u / 0.18)
        return p.pigment * min(1.0, 0.8 * costal + 0.6 * basal)
    res.tint = tint
    return res
