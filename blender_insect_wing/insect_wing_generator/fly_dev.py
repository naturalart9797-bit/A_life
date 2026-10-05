"""
Developmental ("self-organising") model of the dipteran wing venation.

Instead of drawing named veins at fixed positions, the venation is grown
from a few mechanisms that are known (or strongly suggested) to shape fly
wings.  Only the *number of vein systems* and their *order* are genetic
prepattern; their geometry, branching, fusion and the cells they enclose
emerge from the interaction of the rules below.

1. Flow field (proximo-distal axis)
   A harmonic potential phi is solved on the wing blade: phi = 1 at the
   hinge, 0 along the margin.  Its streamlines run from the hinge to the
   margin, converge at the base and fan out distally - the same geometry
   as hemolymph flow through the wing and as the proximo-distal tissue
   flow that elongates the pupal wing (hinge contraction).  Longitudinal
   veins follow these streamlines.

2. Morphogen prepattern (antero-posterior axis)
   Along the margin coordinate psi (0 = anterior base, 1 = posterior base)
   a Dpp-like gradient peaks at the A/P compartment boundary.  The vein
   *systems* Sc, R, M, Cu and A are specified where the gradient crosses
   fixed thresholds (like spalt / optomotor-blind boundaries in
   Drosophila).  Changing the gradient length moves all veins together.

3. Intercalation and branching (Hoffmann et al., PNAS 2018)
   Wherever a point of the margin is farther than a threshold from every
   existing vein, a new vein is induced there (minimum of the inhibitory
   signal).  It grows inward along the ridge between its neighbours, and
   when the gap between them narrows below a second threshold it fuses
   with the nearer one.  This turns the vein systems into branched trees
   (Rs -> R2+3 / R4+5 -> R4 / R5, M1 / M2 / M3, ...): the fork positions
   are not prescribed, they appear where the domain becomes narrow.

4. Cross veins (BMP-dependent)
   Cross veins form between two neighbouring veins where they are close
   enough for their signals to overlap, but only where the Dpp/BMP level
   is high (near the A/P boundary - in Drosophila only the ACV / PCV).
   Lateral inhibition allows only a few per pair.  They close cells such
   as the discal cell dm and the basal medial cell bm.

5. Distal fusion
   Neighbouring veins whose margin ends come very close fuse into a short
   common stalk, closing the cell between them (cell cup: CuA2 + A1).

6. Vein calibre (Murray's law)
   Each vein segment carries the "flow" of all branches distal to it and
   its radius scales with flow^(1/3), giving the natural hierarchy of
   thick stems and thin twigs.
"""

import math
import random

import numpy as np

try:
    from . import venation as _v
    from . import diptera as _d
except ImportError:  # used outside Blender (tools/preview_svg.py)
    import venation as _v
    import diptera as _d


# systems that never branch in Diptera (the subcosta and the anal vein)
NO_BRANCH = ("Sc", "A")

# vein systems: name, side of the A/P boundary, Dpp threshold
SYSTEMS = [
    ("Sc", "a", 0.039),
    ("R", "a", 0.136),
    ("M", "p", 0.887),
    ("Cu", "p", 0.30),
    ("A", "p", 0.127),
]


class DevParams:
    def __init__(self, **kw):
        # outline (same meaning as diptera.FlyParams)
        self.family = "TABANIDAE"
        self.length = 1.0
        self.chord = 0.38
        self.base_power = 0.32
        self.tip_power = 0.75
        self.tip_drop = 0.20
        self.le_bulge = 0.0
        self.alula = 0.08
        self.alula_pos = 0.06
        self.calypter = 0.08
        self.pigment = 0.8
        # simulation
        self.resolution = 200
        self.hinge = 0.03          # extent of the hinge region (x L)
        # positional values per length of posterior margin, relative to
        # the anterior margin (the hind margin grew more)
        self.posterior_weight = 1.2
        # extra expansion of the posterior tissue: conductivity
        # sigma = exp(posterior_growth * chordwise position)
        self.posterior_growth = 0.8
        # morphogen prepattern (psi units = fraction of the margin)
        self.ap_boundary = 0.50
        self.dpp_anterior = 0.10
        self.dpp_posterior = 0.10
        # intercalation / branching (x chord)
        self.branch_gap = 0.34
        self.join_gap = 0.17
        self.branch_levels = 3
        self.min_branch = 0.10      # shorter branches are pruned (x L)
        # cross veins
        self.crossvein_gap = 0.20
        self.crossvein_dpp = 0.30
        self.crossvein_max = 1
        # distal fusion (x chord)
        self.cup_fusion = 0.10
        # anal vein: fraction of its length that is sclerotised from the base
        self.anal_reach = 1.0
        # costa
        self.costa_end = 0.0
        self.circumambient = True
        # randomness
        self.variation = 0.3
        self.seed = 1
        for k, val in kw.items():
            if not hasattr(self, k):
                raise AttributeError("Unknown developmental parameter: %s" % k)
            setattr(self, k, val)


# ---------------------------------------------------------------------------
# 1. Flow field
# ---------------------------------------------------------------------------

def _solve_field(valid, fixed, values, sigma, iters):
    """Solve div(sigma grad psi) = 0 on the raster.  Cells in `fixed` keep
    their value; the others relax to the conductance-weighted mean of their
    valid neighbours (red-black SOR)."""
    u = values.copy()
    vf = valid.astype(float)
    shifts = ((0, 1), (0, -1), (1, 1), (1, -1))
    w = []
    for ax, sh in shifts:
        w.append(0.5 * (sigma + np.roll(sigma, sh, ax)) * np.roll(vf, sh, ax))
    wsum = np.maximum(sum(w), 1e-12)
    upd = valid & ~fixed
    ii, jj = np.indices(valid.shape)
    red = upd & ((ii + jj) % 2 == 0)
    black = upd & ((ii + jj) % 2 == 1)
    n = max(valid.shape)
    omega = 2.0 / (1.0 + math.sin(math.pi / n))
    for _ in range(iters):
        for m in (red, black):
            nb = sum(wk * np.roll(u, sh, ax) for wk, (ax, sh) in zip(w, shifts)) / wsum
            u[m] += omega * (nb[m] - u[m])
    return u


class _PsiField:
    """Positional field psi with bilinear sampling of value and gradient."""

    def __init__(self, grid, psi, valid):
        self.grid = grid
        f = psi.copy()
        ok = valid.copy()
        for _ in range(4):
            fv = np.where(ok, f, 0.0)
            ov = ok.astype(float)
            s = (np.roll(fv, 1, 0) + np.roll(fv, -1, 0) + np.roll(fv, 1, 1) + np.roll(fv, -1, 1))
            c = (np.roll(ov, 1, 0) + np.roll(ov, -1, 0) + np.roll(ov, 1, 1) + np.roll(ov, -1, 1))
            grow = ~ok & (c > 0)
            f[grow] = s[grow] / c[grow]
            ok |= grow
        self.f = f
        gy, gx = np.gradient(f)
        self.gx, self.gy = gx / grid.h, gy / grid.h

    def _sample(self, a, g):
        ny, nx = a.shape
        x = min(max(g[0], 0.0), nx - 1.001)
        y = min(max(g[1], 0.0), ny - 1.001)
        i, j = int(x), int(y)
        fx, fy = x - i, y - j
        return ((a[j, i] * (1 - fx) + a[j, i + 1] * fx) * (1 - fy)
                + (a[j + 1, i] * (1 - fx) + a[j + 1, i + 1] * fx) * fy)

    def value(self, p):
        return self._sample(self.f, self.grid.to_grid(p))

    def grad(self, p):
        g = self.grid.to_grid(p)
        return (self._sample(self.gx, g), self._sample(self.gy, g))


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _closest(p, pts):
    """Closest point on polyline pts (Nx2 array) -> (point, distance, index)."""
    a = pts[:-1]
    b = pts[1:]
    ab = b - a
    l2 = np.maximum((ab * ab).sum(1), 1e-18)
    t = np.clip(((p - a) * ab).sum(1) / l2, 0.0, 1.0)
    c = a + ab * t[:, None]
    d = np.hypot(c[:, 0] - p[0], c[:, 1] - p[1])
    k = int(np.argmin(d))
    return c[k], float(d[k]), k


class DVein:
    def __init__(self, system, s, pts):
        self.system = system      # Sc / R / M / Cu / A
        self.s = s                # margin arc position of the distal end
        self.pts = pts            # list, ordered margin -> base
        self.parent = None        # vein this one fuses into
        self.join_idx = None      # index on parent where it joins
        self.children = []
        self.level = 0
        self.reaches_margin = True

    def arr(self):
        return np.asarray(self.pts)

    def trim_start(self, n, new_start=None):
        """Drop n points at the distal end (keeps children's junctions)."""
        n = max(0, min(n, len(self.pts) - 2))
        self.pts = self.pts[n:]
        shift = n
        if new_start is not None:
            self.pts = [new_start] + self.pts
            shift -= 1
        for c in self.children:
            c.join_idx = max(0, c.join_idx - shift)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate(p):
    rng = random.Random(p.seed)
    shape = _d.FlyShape(p)
    outline, tip_idx = shape.outline(220)
    margin = _v.Margin(outline)
    L, C = p.length, p.chord
    var = p.variation

    grid = _v.Grid(outline, p)
    inside = np.frombuffer(bytes(grid.inside), dtype=np.uint8).reshape(grid.ny, grid.nx) > 0
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.h
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.h
    hinge = inside & (xs[None, :] < p.hinge * L)

    # --- 1. positional field psi ----------------------------------------
    # The margin (the former D/V boundary) carries the A/P positional value
    # psi = margin coordinate (0 anterior base -> 1 posterior base); the
    # posterior margin carries more of it (posterior_weight).  Inside the
    # blade psi relaxes as div(sigma grad psi) = 0, where sigma models the
    # extra expansion of the posterior tissue: where tissue has grown more,
    # positional values are spread thinner and veins lie farther apart.
    s_tip = margin.cum[tip_idx]
    wpost = max(0.1, p.posterior_weight)
    tot_w = s_tip + wpost * (margin.total - s_tip)

    def to_psi(s_):
        return (s_ if s_ <= s_tip else s_tip + wpost * (s_ - s_tip)) / tot_w

    def to_s(psi_):
        w_ = min(max(psi_, 0.005), 0.995) * tot_w
        return w_ if w_ <= s_tip else s_tip + (w_ - s_tip) / wpost

    near = (np.roll(inside, 1, 0) | np.roll(inside, -1, 0)
            | np.roll(inside, 1, 1) | np.roll(inside, -1, 1)) & ~inside
    jj, ii = np.nonzero(near)
    mp = np.asarray(margin.pts[:-1])
    mpsi = np.asarray([to_psi(c_) for c_ in margin.cum[:-1]])
    d2 = (xs[ii][:, None] - mp[None, :, 0]) ** 2 + (ys[jj][:, None] - mp[None, :, 1]) ** 2
    values = np.zeros(inside.shape)
    values[jj, ii] = mpsi[np.argmin(d2, axis=1)]
    uu = np.clip(xs / L, 0.0, 1.0)
    yle = np.array([shape.y_le(u_) for u_ in uu])
    wid = np.array([max(shape.core_width(u_), 1e-6) for u_ in uu])
    vrel = np.clip((yle[None, :] - ys[:, None]) / wid[None, :], 0.0, 1.0)
    # across the hinge psi runs from anterior (0) to posterior (1): every
    # vein system passes through the narrow wing base in A/P order
    values[hinge] = vrel[hinge]
    values[inside & ~hinge] = 0.5 * vrel[inside & ~hinge] + 0.25
    sigma = np.exp(p.posterior_growth * vrel)
    fixed = near | hinge
    psi = _solve_field(inside | near, fixed, values, sigma,
                       iters=int(5 * max(grid.nx, grid.ny)))
    field = _PsiField(grid, psi, inside | near)
    band = inside | near          # veins may graze the margin (costa)
    poly = margin.pts[:-1]
    step = grid.h * 0.6

    def in_hinge(q):
        return q[0] < p.hinge * L

    def trace(start, c, inward, neighbours=None, max_len=3.0 * L):
        """Follow the iso-line psi = c from the margin to the hinge: a vein
        lies where the positional value equals its threshold.  With
        neighbours, stop and fuse when the gap between them closes."""
        pts = [start]
        q = start
        # veins run proximally: start inward and toward the hinge
        prev = _v._norm((inward[0] - 1.5, inward[1]))
        travelled = 0.0
        while travelled < max_len:
            g = field.grad(q)
            gn = math.hypot(*g)
            if gn < 1e-9:
                break
            t = (-g[1] / gn, g[0] / gn)
            if t[0] * prev[0] + t[1] * prev[1] < 0:
                t = (-t[0], -t[1])
            if neighbours and travelled > 2 * step:
                a, b = neighbours
                ca, da, ia = _closest(np.asarray(q), a.arr())
                cb, db, ib = _closest(np.asarray(q), b.arr())
                if da + db < p.join_gap * C:
                    # the domain has become narrow: fuse with the nearer
                    # neighbour, slightly proximally (Y-shaped junction)
                    tgt, idx = (a, ia) if da <= db else (b, ib)
                    if tgt.system in NO_BRANCH:
                        tgt, idx = (b, ib) if tgt is a else (a, ia)
                        if tgt.system in NO_BRANCH:
                            return pts, None
                    tp = tgt.arr()
                    k = min(len(tp) - 1, idx + 1 + int(0.015 * L / step))
                    J = tp[k]
                    # leave the parent tangentially: quadratic Bezier from a
                    # point some way back on this vein to the junction, with
                    # the control point on the parent's distal direction
                    back = min(len(pts) - 2, int(0.10 * L / step))
                    P0 = np.asarray(pts[-1 - back])
                    tin = P0 - np.asarray(pts[-3 - back]) if len(pts) > back + 2 else J - P0
                    tin = tin / max(np.hypot(*tin), 1e-12)
                    pdir = tp[max(0, k - 3)] - J
                    pdir = pdir / max(np.hypot(*pdir), 1e-12)
                    dd = float(np.hypot(*(P0 - J))) / 3.0
                    P1 = P0 + tin * dd
                    P2 = J + pdir * dd
                    del pts[len(pts) - back:]
                    for i in range(1, 11):
                        t = i / 10.0
                        mt = 1 - t
                        b = mt ** 3 * P0 + 3 * mt * mt * t * P1 + 3 * mt * t * t * P2 + t ** 3 * J
                        pts.append((float(b[0]), float(b[1])))
                    return pts, (tgt, k)
            nq = (q[0] + t[0] * step, q[1] + t[1] * step)
            # Newton correction back onto the iso-line
            err = c - field.value(nq)
            g2 = field.grad(nq)
            gg = g2[0] ** 2 + g2[1] ** 2
            if gg > 1e-12:
                gn2 = math.sqrt(gg)
                corr = max(-0.5 * step, min(0.5 * step, err / gn2))
                nq = (nq[0] + g2[0] / gn2 * corr, nq[1] + g2[1] / gn2 * corr)
            gi = grid.to_grid(nq)
            ci, cj = int(round(gi[0])), int(round(gi[1]))
            if not (0 <= ci < grid.nx and 0 <= cj < grid.ny and band[cj, ci]):
                break
            prev = t
            q = nq
            pts.append(q)
            travelled += step
            if in_hinge(q):
                break
        return pts, None

    def start_at(s):
        m = margin.point(s)
        n = margin.inward_normal(s)
        return (m[0] + n[0] * step * 0.5, m[1] + n[1] * step * 0.5), m, n

    # --- 2. morphogen prepattern -> vein systems -------------------------
    def psi_to_s(psi):
        return to_s(psi)

    def dpp(psi):
        d = psi - p.ap_boundary
        lam = p.dpp_anterior if d < 0 else p.dpp_posterior
        return math.exp(-abs(d) / lam)

    veins = []
    for name, side, thr in SYSTEMS:
        lam = p.dpp_anterior if side == "a" else p.dpp_posterior
        pv = p.ap_boundary + (1 if side == "p" else -1) * lam * math.log(1.0 / thr)
        pv += var * 0.012 * (rng.random() - 0.5)
        s = psi_to_s(pv)
        st, m, n = start_at(s)
        pts, _ = trace(st, to_psi(s), n)
        v = DVein(name, s, [m] + pts)
        veins.append(v)
    veins.sort(key=lambda v: v.s)

    # --- 3. intercalation / branching -----------------------------------
    for level in range(int(p.branch_levels)):
        new_list = [veins[0]]
        added = False
        for a, b in zip(veins[:-1], veins[1:]):
            if a.system == "Sc" and b.system == "Sc":
                new_list.append(b)
                continue
            best, best_s = 0.0, None
            for k in range(1, 40):
                s = a.s + (b.s - a.s) * k / 40.0
                m = np.asarray(margin.point(s))
                d = min(_closest(m, a.arr())[1], _closest(m, b.arr())[1])
                if d > best:
                    best, best_s = d, s
            thr = 0.5 * p.branch_gap * C * (1.0 + var * 0.3 * (rng.random() - 0.5))
            if best_s is not None and best > thr:
                st, m, n = start_at(best_s)
                pts, join = trace(st, to_psi(best_s), n, (a, b))
                if join is not None and len(pts) * step < p.min_branch * L:
                    # a primordium that fuses at once does not become a vein
                    new_list.append(b)
                    continue
                v = DVein(a.system, best_s, [m] + pts)
                v.level = level + 1
                if join is not None:
                    v.parent, v.join_idx = join
                    v.system = v.parent.system
                    v.parent.children.append(v)
                new_list.append(v)
                added = True
            new_list.append(b)
        veins = new_list
        if not added:
            break

    # --- 5. distal fusion (closed cells such as cup) ---------------------
    stalks = []
    for a, b in zip(veins[:-1], veins[1:]):
        if a.system == "Sc" or b.system == "Sc":
            continue
        ea, eb = np.asarray(a.pts[0]), np.asarray(b.pts[0])
        if np.hypot(*(ea - eb)) < p.cup_fusion * C:
            s_mid = 0.5 * (a.s + b.s)
            E = margin.point(s_mid)
            n = margin.inward_normal(s_mid)
            Lst = p.cup_fusion * C * 0.9
            J = (E[0] + n[0] * Lst, E[1] + n[1] * Lst)
            for v in (a, b):
                n = 0
                while n < len(v.pts) - 2 and math.hypot(v.pts[n][0] - E[0],
                                                        v.pts[n][1] - E[1]) < Lst * 1.3:
                    n += 1
                v.trim_start(n, J)
                v.reaches_margin = False
            stalks.append([E, J])

    # --- anal vein reach (A1 fading before the margin, e.g. Muscidae) ---
    for v in veins:
        if v.system == "A" and p.anal_reach < 1.0 and v.reaches_margin:
            v.trim_start(int(len(v.pts) * (1.0 - p.anal_reach)))
            v.reaches_margin = False

    # --- 4. cross veins ---------------------------------------------------
    cross = []
    min_from_margin = 0.22 * L
    for a, b in zip(veins[:-1], veins[1:]):
        if "Sc" in (a.system, b.system):
            continue
        psi_mid = to_psi(0.5 * (a.s + b.s))
        if dpp(psi_mid) < p.crossvein_dpp:
            continue
        pa, pb = a.arr(), b.arr()
        junctions = [np.asarray(v.pts[-1]) for v in (a, b) if v.parent in (a, b)]
        placed = []
        travelled = 0.0
        for i in range(1, len(pa)):
            travelled += float(np.hypot(*(pa[i] - pa[i - 1])))
            q = pa[i]
            if travelled < min_from_margin or q[0] < 0.22 * L or q[0] > 0.75 * L:
                continue
            c, d, _ = _closest(q, pb)
            if d > p.crossvein_gap * C or d < 0.35 * p.join_gap * C:
                continue
            if any(np.hypot(*(q - j)) < 1.5 * p.crossvein_gap * C for j in junctions):
                continue
            mid = 0.5 * (q + c)
            if any(np.hypot(*(mid - m2)) < 0.14 * L for m2 in placed):
                continue
            if any(np.hypot(*(mid - 0.5 * (np.asarray(x[0]) + np.asarray(x[1])))) < 0.06 * L
                   for x in cross):
                continue
            cross.append([tuple(q), tuple(c)])
            placed.append(mid)
            if len(placed) >= p.crossvein_max:
                break

    # --- 6. vein calibre: Murray's law ------------------------------------
    flow = {}

    def total(v):
        if id(v) in flow:
            return flow[id(v)][0]
        f = 1.0 + sum(total(c) for c in v.children)
        arr = [1.0] * len(v.pts)
        for c in v.children:
            fc = total(c)
            for k in range(min(c.join_idx, len(arr) - 1), len(arr)):
                arr[k] += fc
        flow[id(v)] = (f, arr)
        return f

    for v in veins:
        total(v)

    # --- assemble result ---------------------------------------------------
    res = _d._Result()
    res.shape = shape
    res.outline = outline
    costa_s = s_tip + p.costa_end * L
    costa = _v.Vein(margin.sub_polyline(0.0, costa_s), "costa", 0)
    res.veins.append(costa)
    rest = margin.sub_polyline(costa_s, margin.total)
    res.veins.append(_v.Vein(rest, "margin" if p.circumambient else "rim", 3))

    counters = {}
    for v in veins:
        root = v
        while root.parent is not None:
            root = root.parent
        counters[root.system] = counters.get(root.system, 0) + 1
        if v.system == "Sc":
            kind = "sc"
        elif v.parent is None and v.system == "R":
            kind = "r1"
        elif v.system == "R":
            kind = "radial"
        else:
            kind = "main"
        vein = _v.Vein(list(v.pts), kind, 1 + v.level)
        vein.name = "%s%s" % (root.system, "" if v.parent is None else
                              "-%d" % counters[root.system])
        vein.radii = [f ** (1.0 / 3.0) for f in flow[id(v)][1]]
        res.veins.append(vein)
        res.labels[vein.name] = vein
    for k, c in enumerate(cross):
        vein = _v.Vein(c, "cross", 4)
        vein.name = "x%d" % (k + 1)
        res.veins.append(vein)
    for st in stalks:
        vein = _v.Vein(st, "main", 1)
        vein.name = "stalk"
        vein.radii = [2.0 ** (1.0 / 3.0)] * 2
        res.veins.append(vein)
    # humeral cross vein: the basal brace between costa and Sc
    sc = next((v for v in veins if v.system == "Sc"), None)
    if sc is not None:
        xh = 0.07 * L
        top = margin.point(margin.param_of_x(xh, True))
        c, _, _ = _closest(np.asarray(top), sc.arr())
        if abs(c[0] - xh) < 0.05 * L:
            hv = _v.Vein([top, tuple(c)], "cross", 4)
            hv.name = "h"
            res.veins.append(hv)

    res.centres = []
    res.psi = psi
    res.grid = grid
    _d.add_basal_parts(res, shape, p)
    return res


# ---------------------------------------------------------------------------
# Family presets: the same mechanisms, different parameters
# ---------------------------------------------------------------------------

PRESETS = {
    # many branches (low intercalation threshold), cup closed by fusion,
    # costa all round
    "TABANIDAE": dict(chord=0.38, base_power=0.32, tip_power=0.75, tip_drop=0.20,
                      le_bulge=0.0, alula=0.08, alula_pos=0.06, calypter=0.08,
                      ap_boundary=0.50, dpp_anterior=0.10, dpp_posterior=0.10,
                      branch_gap=0.34, join_gap=0.17, branch_levels=3,
                      cup_fusion=0.12,
                      anal_reach=1.0, costa_end=0.0, circumambient=True),
    # few branches (high threshold), A1 fades, short costa
    "MUSCIDAE": dict(chord=0.40, base_power=0.38, tip_power=0.62, tip_drop=0.32,
                     le_bulge=0.02, alula=0.14, alula_pos=0.07, calypter=0.15,
                     ap_boundary=0.384, dpp_anterior=0.108, dpp_posterior=0.098,
                     branch_gap=0.62, join_gap=0.20, branch_levels=2,
                     cup_fusion=0.0,
                     anal_reach=0.55, costa_end=0.05, circumambient=False),
    "SYRPHIDAE": dict(chord=0.36, base_power=0.36, tip_power=0.68, tip_drop=0.22,
                      le_bulge=0.01, alula=0.12, alula_pos=0.07, calypter=0.11,
                      ap_boundary=0.462, dpp_anterior=0.124, dpp_posterior=0.076,
                      branch_gap=0.46, join_gap=0.20, branch_levels=2,
                      cup_fusion=0.12,
                      anal_reach=1.0, costa_end=0.02, circumambient=False),
}
