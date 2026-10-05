"""
Insect wing venation generator (pure Python, no Blender dependency).

Based on the developmental model of
    Hoffmann J., Donoughe S., Li K., Salcedo M. K., Rycroft C. H. (2018)
    "A simple developmental model recapitulates complex insect wing
    venation patterns", PNAS 115(40): 9905-9910.
    https://www.pnas.org/doi/10.1073/pnas.1721248115

The paper's key observations, which this module implements:

1. Intercalary veins
   New longitudinal (intercalary) veins appear in the gaps between existing
   veins once the gap exceeds a threshold width, and they run along the
   locus that is *farthest from existing veins* (the ridge / medial axis of
   the distance field).  They grow inward from the wing margin and stop
   when the gap narrows below a threshold.

2. Cross veins (polygonal cells)
   The remaining domains between longitudinal veins are partitioned by
   cross veins.  Cell "centres" are placed where an inhibitory signal from
   existing veins is weakest, i.e. at local maxima of the distance to the
   nearest vein, and each placed centre itself inhibits further centres
   nearby.  The cross veins are then the boundaries of the Voronoi
   tessellation of those centres, restricted to each inter-vein domain.
   This reproduces ladder-like cross veins in narrow strips and
   hexagon-like polygonal meshes in wide regions, as seen in dragonfly
   and damselfly wings.

All geometry is produced in 2D wing coordinates:
    x : span direction (0 = wing base / hinge,  L = wing tip)
    y : chord direction (0 ~ leading edge, negative toward trailing edge)
"""

import heapq
import math
import random


# ---------------------------------------------------------------------------
# Small vector helpers
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _mul(a, s):
    return (a[0] * s, a[1] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _len(a):
    return math.hypot(a[0], a[1])


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _norm(a):
    n = _len(a)
    if n < 1e-12:
        return (0.0, 0.0)
    return (a[0] / n, a[1] / n)


def _polyline_length(pts):
    return sum(_len(_sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))


def _resample_polyline(pts, step):
    """Resample a polyline at (approximately) constant arc-length step."""
    if len(pts) < 2:
        return list(pts)
    total = _polyline_length(pts)
    n = max(2, int(math.ceil(total / max(step, 1e-9))) + 1)
    return [_point_at_length(pts, total * i / (n - 1)) for i in range(n)]


def _point_at_length(pts, s):
    """Point at arc length s along polyline (clamped)."""
    if s <= 0:
        return pts[0]
    acc = 0.0
    for i in range(len(pts) - 1):
        seg = _len(_sub(pts[i + 1], pts[i]))
        if acc + seg >= s:
            t = (s - acc) / seg if seg > 1e-12 else 0.0
            return _lerp(pts[i], pts[i + 1], t)
        acc += seg
    return pts[-1]


def _closest_on_segment(p, a, b):
    ab = _sub(b, a)
    l2 = _dot(ab, ab)
    if l2 < 1e-18:
        return a
    t = max(0.0, min(1.0, _dot(_sub(p, a), ab) / l2))
    return (a[0] + ab[0] * t, a[1] + ab[1] * t)


def point_in_polygon(p, poly):
    x, y = p
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xin = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xin:
                inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

class WingParams:
    """All parameters controlling one wing.  Lengths are in wing units."""

    def __init__(self, **kw):
        # --- outline ---
        self.length = 1.0          # span (base -> tip)
        self.chord = 0.24          # maximum chord width
        self.base_power = 0.35     # how fast the wing widens from the hinge
        self.tip_power = 0.55      # roundness of the tip (smaller = rounder)
        self.tip_drop = 0.25       # tip position below leading edge (x chord)
        self.le_bulge = 0.04       # leading edge convexity (x chord)
        # --- primary longitudinal veins ---
        self.n_primary = 7         # number of main longitudinal veins
        self.subcosta_end = 0.48   # where the subcosta meets the margin (nodus)
        self.radius_end = 0.93     # where the radius meets the margin
        self.last_vein_end = 0.12  # x/L where the last (anal) vein meets TE
        self.vein_spread = 1.0     # spacing exponent of vein ends along margin
        self.branch_prob = 0.45    # probability that a vein branches off parent
        self.vein_curvature = 0.5
        self.hinge = 0.03          # x/L of the basal segment the veins leave
        # --- intercalary veins (paper: placed farthest from existing veins)
        self.intercalary_levels = 2
        self.intercalary_threshold = 0.07   # gap width (x chord) that spawns one
        self.intercalary_stop = 0.55        # stops when gap < threshold * stop
        # --- cross veins (Voronoi of inhibition-minimum centres) ---
        self.resolution = 450      # raster cells along the span
        self.cell_size = 0.09       # max cell radius in wide areas (x chord)
        self.ladder_ratio = 0.85    # cell spacing / local half-width
        self.min_cell_radius = 1.2  # in raster cells
        self.cell_noise = 0.25      # irregularity of centre placement
        # --- pterostigma ---
        self.pterostigma = True
        self.ptero_start = 0.80
        self.ptero_end = 0.88
        self.ptero_width = 0.10     # x chord
        self.seed = 1
        for k, v in kw.items():
            if not hasattr(self, k):
                raise AttributeError("Unknown wing parameter: %s" % k)
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# Wing outline
# ---------------------------------------------------------------------------

class WingShape:
    def __init__(self, p):
        self.p = p
        a = max(0.01, p.base_power)
        b = max(0.01, p.tip_power)
        um = a / (a + b)
        self._fmax = (um ** a) * ((1.0 - um) ** b)
        self._a, self._b = a, b

    def width(self, u):
        u = min(max(u, 0.0), 1.0)
        return self.p.chord * (u ** self._a) * ((1.0 - u) ** self._b) / self._fmax

    def y_le(self, u):
        c = self.p.chord
        return c * self.p.le_bulge * math.sin(math.pi * u) - c * self.p.tip_drop * u ** 4

    def y_te(self, u):
        return self.y_le(u) - self.width(u)

    def outline(self, n=160):
        """Closed polygon: leading edge base->tip, trailing edge tip->base.
        Returns (polygon, index_of_tip)."""
        L = self.p.length
        us = [0.5 - 0.5 * math.cos(math.pi * i / (n - 1)) for i in range(n)]
        le = [(u * L, self.y_le(u)) for u in us]
        te = [(u * L, self.y_te(u)) for u in reversed(us)]
        poly = le + te[1:-1]
        return poly, n - 1


class Margin:
    """Wing margin as a closed polyline with arc-length parametrisation.
    s = 0 at the base, increasing along the leading edge, round the tip
    and back along the trailing edge."""

    def __init__(self, poly):
        self.pts = list(poly) + [poly[0]]
        self.cum = [0.0]
        for i in range(len(self.pts) - 1):
            self.cum.append(self.cum[-1] + _len(_sub(self.pts[i + 1], self.pts[i])))
        self.total = self.cum[-1]

    def point(self, s):
        s = s % self.total
        lo, hi = 0, len(self.cum) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cum[mid] <= s:
                lo = mid
            else:
                hi = mid
        seg = self.cum[hi] - self.cum[lo]
        t = (s - self.cum[lo]) / seg if seg > 1e-12 else 0.0
        return _lerp(self.pts[lo], self.pts[hi], t)

    def inward_normal(self, s):
        e = self.total * 1e-3
        a = self.point(s - e)
        b = self.point(s + e)
        t = _norm(_sub(b, a))
        # polygon is clockwise (LE on top going +x, TE back) -> inside is right
        return (t[1], -t[0])

    def param_of_x(self, x, on_leading=True):
        """Arc length of the margin point at span position x."""
        best, best_d = 0.0, 1e18
        for i in range(len(self.pts) - 1):
            p = self.pts[i]
            if on_leading and self.cum[i] > self.total * 0.5:
                continue
            if not on_leading and self.cum[i] < self.total * 0.5:
                continue
            d = abs(p[0] - x)
            if d < best_d:
                best_d, best = d, self.cum[i]
        return best

    def sub_polyline(self, s0, s1):
        pts = [self.point(s0)]
        for i, c in enumerate(self.cum):
            if s0 < c < s1:
                pts.append(self.pts[i])
        pts.append(self.point(s1))
        return pts


# ---------------------------------------------------------------------------
# Primary longitudinal veins
# ---------------------------------------------------------------------------

class Vein:
    def __init__(self, pts, kind, order=0):
        self.pts = pts            # polyline, ordered base -> margin
        self.kind = kind          # costa/primary/intercalary/cross/margin
        self.order = order        # hierarchy level (for thickness)
        self.margin_s = None      # arc param where it meets the margin


def _make_primary_veins(shape, margin, p, rng):
    L = p.length
    # veins leave the hinge spread over a short basal segment
    uh = max(0.005, p.hinge)
    yh_top = shape.y_le(uh)
    wh = shape.width(uh)
    s_sc = margin.param_of_x(p.subcosta_end * L, True)
    s_r = margin.param_of_x(p.radius_end * L, True)
    s_last = margin.param_of_x(p.last_vein_end * L, False)

    n = max(2, int(p.n_primary))
    ends = [s_sc, s_r]
    rest = n - 2
    for i in range(rest):
        t = (i + 1) / (rest + 1) if rest > 0 else 0.5
        t = t ** p.vein_spread
        jitter = (rng.random() - 0.5) * 0.35 / (rest + 1)
        ends.append(s_r + (s_last - s_r) * min(max(t + jitter, 0.02), 0.98))
    if rest > 0:
        ends[-1] = s_last
    ends.sort()

    veins = []
    for i, s_end in enumerate(ends):
        E = margin.point(s_end)
        fr = 0.12 + 0.76 * (i / max(1, n - 1))
        start = (uh * L, yh_top - wh * fr)
        if i >= 2 and rng.random() < p.branch_prob and veins:
            # branch off the previous vein (e.g. RP / MA splitting)
            parent = veins[-1]
            tx = E[0] * (0.15 + rng.random() * 0.35)
            if parent.pts[0][0] < tx < parent.pts[-1][0]:
                for q0, q1 in zip(parent.pts[:-1], parent.pts[1:]):
                    if q0[0] <= tx <= q1[0]:
                        f = (tx - q0[0]) / max(q1[0] - q0[0], 1e-12)
                        start = _lerp(q0, q1, f)
                        break
        # Power-law path: the chordwise offset develops later than the
        # spanwise one, so veins run along the leading edge first and then
        # bend toward the margin.  The same exponent for every vein keeps
        # the order of neighbouring veins and prevents crossings.
        expo = 1.0 + 3.0 * max(0.0, p.vein_curvature)
        pts = []
        for k in range(64):
            t = k / 63.0
            pts.append((start[0] + (E[0] - start[0]) * t,
                        start[1] + (E[1] - start[1]) * t ** expo))
        # keep inside the wing
        poly = margin.pts[:-1]
        pts = [q for q in pts[:-1] if point_in_polygon(q, poly)] + [E]
        if len(pts) < 2:
            continue
        v = Vein(pts, "primary", 1)
        v.margin_s = s_end
        veins.append(v)
    if veins:
        veins[0].kind = "subcosta"
    return veins


# ---------------------------------------------------------------------------
# Intercalary veins: along the ridge farthest from both neighbours
# ---------------------------------------------------------------------------

def _from_margin(v):
    """Polyline ordered margin -> base."""
    return list(reversed(v.pts))


def _intercalary_between(a, b, margin, p, step):
    thr = p.intercalary_threshold * p.chord
    stop = thr * p.intercalary_stop
    gap = margin.point(a.margin_s)
    gap = _len(_sub(margin.point(b.margin_s), gap))
    if gap < thr:
        return None
    pa = _from_margin(a)
    pb = _from_margin(b)
    la, lb = _polyline_length(pa), _polyline_length(pb)
    lmax = min(la, lb)
    pts = []
    s_mid = 0.5 * (a.margin_s + b.margin_s)
    pts.append(margin.point(s_mid))
    s = step
    while s < lmax:
        qa = _point_at_length(pa, s * la / lmax)
        qb = _point_at_length(pb, s * lb / lmax)
        w = _len(_sub(qa, qb))
        if w < stop:
            break
        pts.append(_lerp(qa, qb, 0.5))
        s += step
    if len(pts) < 3:
        return None
    # smooth the first few points into the margin point
    v = Vein(list(reversed(pts)), "intercalary", 2)
    v.margin_s = s_mid
    return v


def _make_intercalaries(longis, margin, p):
    step = p.length / 200.0
    current = sorted(longis, key=lambda v: v.margin_s)
    added = []
    for level in range(int(p.intercalary_levels)):
        new_list = [current[0]]
        new_found = []
        for a, b in zip(current[:-1], current[1:]):
            iv = _intercalary_between(a, b, margin, p, step)
            if iv is not None:
                iv.order = 2 + level
                new_list.append(iv)
                new_found.append(iv)
            new_list.append(b)
        if not new_found:
            break
        added.extend(new_found)
        current = new_list
    return added


# ---------------------------------------------------------------------------
# Raster utilities
# ---------------------------------------------------------------------------

class Grid:
    def __init__(self, poly, p):
        xs = [q[0] for q in poly]
        ys = [q[1] for q in poly]
        self.h = p.length / float(p.resolution)
        pad = 3
        self.x0 = min(xs) - pad * self.h
        self.y0 = min(ys) - pad * self.h
        self.nx = int(math.ceil((max(xs) - self.x0) / self.h)) + pad
        self.ny = int(math.ceil((max(ys) - self.y0) / self.h)) + pad
        self.n = self.nx * self.ny
        self.inside = self._scan_fill(poly)

    def center(self, idx):
        j, i = divmod(idx, self.nx)
        return (self.x0 + (i + 0.5) * self.h, self.y0 + (j + 0.5) * self.h)

    def to_grid(self, pt):
        return ((pt[0] - self.x0) / self.h - 0.5, (pt[1] - self.y0) / self.h - 0.5)

    def from_grid(self, g):
        return (self.x0 + (g[0] + 0.5) * self.h, self.y0 + (g[1] + 0.5) * self.h)

    def _scan_fill(self, poly):
        inside = bytearray(self.n)
        m = len(poly)
        for j in range(self.ny):
            y = self.y0 + (j + 0.5) * self.h
            xs = []
            for k in range(m):
                xa, ya = poly[k]
                xb, yb = poly[(k + 1) % m]
                if (ya > y) != (yb > y):
                    xs.append(xa + (y - ya) * (xb - xa) / (yb - ya))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                i0 = int(math.ceil((xs[k] - self.x0) / self.h - 0.5))
                i1 = int(math.floor((xs[k + 1] - self.x0) / self.h - 0.5))
                row = j * self.nx
                for i in range(max(i0, 0), min(i1, self.nx - 1) + 1):
                    inside[row + i] = 1
        return inside

    def draw_polyline(self, pts, wall):
        """Mark a 4-connected wall of cells along the polyline."""
        nx, ny = self.nx, self.ny
        for k in range(len(pts) - 1):
            a = self.to_grid(pts[k])
            b = self.to_grid(pts[k + 1])
            n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2) + 1
            for t in range(n + 1):
                gx = a[0] + (b[0] - a[0]) * t / n
                gy = a[1] + (b[1] - a[1]) * t / n
                i, j = int(round(gx)), int(round(gy))
                for di, dj in ((0, 0), (1, 0), (0, 1)):
                    ii, jj = i + di, j + dj
                    if 0 <= ii < nx and 0 <= jj < ny:
                        wall[jj * nx + ii] = 1

    def neighbours8(self, idx):
        nx, ny = self.nx, self.ny
        j, i = divmod(idx, nx)
        for dj in (-1, 0, 1):
            jj = j + dj
            if jj < 0 or jj >= ny:
                continue
            for di in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                ii = i + di
                if 0 <= ii < nx:
                    yield jj * nx + ii


def _euclid_propagate(grid, sources, passable, dist, label=None, nearest=None):
    """Dijkstra-like vector propagation of exact Euclidean distance to the
    nearest source point (sources: list of (idx, (gx, gy), label)).
    Only cells with passable[idx] are entered.  Distances in grid units."""
    nx = grid.nx
    heap = []
    for idx, g, lab in sources:
        j, i = divmod(idx, nx)
        d = math.hypot(i - g[0], j - g[1])
        if d < dist[idx]:
            dist[idx] = d
            if label is not None:
                label[idx] = lab
            if nearest is not None:
                nearest[idx] = g
            heapq.heappush(heap, (d, idx, g, lab))
    while heap:
        d, idx, g, lab = heapq.heappop(heap)
        if d > dist[idx] + 1e-12:
            continue
        for nb in grid.neighbours8(idx):
            if not passable[nb]:
                continue
            j, i = divmod(nb, nx)
            nd = math.hypot(i - g[0], j - g[1])
            if nd < dist[nb] - 1e-12:
                dist[nb] = nd
                if label is not None:
                    label[nb] = lab
                if nearest is not None:
                    nearest[nb] = g
                heapq.heappush(heap, (nd, nb, g, lab))


# ---------------------------------------------------------------------------
# Cross veins: Voronoi of inhibition-minimum centres
# ---------------------------------------------------------------------------

def _make_crossveins(grid, veins, outline, p, rng):
    INF = float("inf")
    n = grid.n
    nx = grid.nx
    wall = bytearray(n)
    for v in veins:
        grid.draw_polyline(v.pts, wall)
    # outside cells act as the margin vein
    free = bytearray(n)
    for k in range(n):
        if grid.inside[k] and not wall[k]:
            free[k] = 1

    # --- distance to nearest vein (inhibitory signal from veins) ---
    dv = [INF] * n
    srcs = []
    for k in range(n):
        if not free[k]:
            # only use boundary cells as sources (adjacent to a free cell)
            for nb in grid.neighbours8(k):
                if free[nb]:
                    j, i = divmod(k, nx)
                    srcs.append((k, (float(i), float(j)), 0))
                    break
    _euclid_propagate(grid, srcs, free, dv)
    for k in range(n):
        if not free[k]:
            dv[k] = 0.0

    # --- local half-width of the inter-vein domain ---
    # Ridge cells of the distance field (the locus farthest from veins)
    # carry the local half-width; it is spread to every cell of the domain.
    ridge = []
    for k in range(n):
        if not free[k] or dv[k] < 1.0:
            continue
        j, i = divmod(k, nx)
        d0 = dv[k]
        for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1)):
            ka = (j + dj) * nx + (i + di)
            kb = (j - dj) * nx + (i - di)
            if 0 <= ka < n and 0 <= kb < n and d0 >= dv[ka] and d0 >= dv[kb]:
                ridge.append((k, (float(i), float(j)), d0))
                break
    wd = [INF] * n
    wloc = [0.0] * n
    _euclid_propagate(grid, ridge, free, wd, label=wloc)

    # --- greedy placement of cell centres (inhibition-minimum sites) ---
    # Each new centre is put where the inhibition from veins and from
    # already-placed centres is weakest.  The admissible spacing r(p) scales
    # with the local domain width (ladders in narrow strips) but is capped
    # by the cell size (polygonal meshes in wide domains).
    rmin = max(0.75, p.min_cell_radius)
    rmax = max(rmin, p.cell_size * p.chord / grid.h)
    ladder = max(0.2, p.ladder_ratio)
    noise = [1.0 + p.cell_noise * (rng.random() - 0.5) for _ in range(n)]
    rr = [min(max(ladder * wloc[k], rmin), rmax) for k in range(n)]
    ds = [INF] * n
    heap = []
    for k in range(n):
        if free[k] and dv[k] * noise[k] >= rr[k]:
            heapq.heappush(heap, (-dv[k] * noise[k] / rr[k], k))
    centres = []
    while heap:
        negf, k = heapq.heappop(heap)
        if -negf < 1.0:
            break
        f = min(dv[k] * noise[k], ds[k]) / rr[k]
        if f < -negf - 1e-9:
            if f >= 1.0:
                heapq.heappush(heap, (-f, k))
            continue
        j, i = divmod(k, nx)
        g = (i + (rng.random() - 0.5) * 0.5, j + (rng.random() - 0.5) * 0.5)
        lab = len(centres)
        centres.append(g)
        _euclid_propagate(grid, [(k, g, lab)], free, ds)

    # --- Voronoi tessellation inside each inter-vein domain ---
    dist = [INF] * n
    label = [-1] * n
    _euclid_propagate(grid, [(_gidx(grid, c), c, lab) for lab, c in enumerate(centres)
                             if free[_gidx(grid, c)]], free, dist, label=label)

    # boundary points between differing labels
    pairs = {}
    for k in range(n):
        lk = label[k]
        if lk < 0:
            continue
        j, i = divmod(k, nx)
        for di, dj in ((1, 0), (0, 1)):
            ii, jj = i + di, j + dj
            if ii >= nx or jj >= grid.ny:
                continue
            q = jj * nx + ii
            lq = label[q]
            if lq >= 0 and lq != lk:
                key = (lk, lq) if lk < lq else (lq, lk)
                pairs.setdefault(key, []).append((i + di * 0.5, j + dj * 0.5))

    # junctions (Voronoi vertices): 2x2 blocks touching >= 3 regions
    junc = []
    for j in range(grid.ny - 1):
        for i in range(nx - 1):
            k = j * nx + i
            labs = {label[k], label[k + 1], label[k + nx], label[k + nx + 1]}
            if len(labs) >= 3:
                junc.append(((i + 0.5, j + 0.5), -1 in labs))
    clusters = _cluster_points(junc, 1.6)

    # vein segments in grid units (for snapping to veins)
    segs = []
    for v in veins:
        g = [grid.to_grid(q) for q in v.pts]
        for a, b in zip(g[:-1], g[1:]):
            segs.append((a, b))
    poly_g = [grid.to_grid(q) for q in outline]
    for a, b in zip(poly_g, poly_g[1:] + poly_g[:1]):
        segs.append((a, b))
    seg_index = _SegmentIndex(segs, 6.0)

    def snap_to_vein(pt, maxd):
        best, bd = None, maxd
        for a, b in seg_index.query(pt):
            c = _closest_on_segment(pt, a, b)
            d = _len(_sub(c, pt))
            if d < bd:
                bd, best = d, c
        return best

    cl_pos = []
    for c, touches_vein in clusters:
        if touches_vein:
            s = snap_to_vein(c, 3.0)
            cl_pos.append(s if s is not None else c)
        else:
            cl_pos.append(c)
    cl_index = _PointIndex(cl_pos, 4.0)

    cross = []
    for (la, lb), pts in pairs.items():
        ca, cb = centres[la], centres[lb]
        m = _lerp(ca, cb, 0.5)
        d = _norm((-(cb[1] - ca[1]), cb[0] - ca[0]))
        if d == (0.0, 0.0):
            continue
        ts = [_dot(_sub(q, m), d) for q in pts]
        t0, t1 = min(ts) - 0.5, max(ts) + 0.5
        e0 = _add(m, _mul(d, t0))
        e1 = _add(m, _mul(d, t1))
        e0 = _snap_end(e0, cl_index, snap_to_vein)
        e1 = _snap_end(e1, cl_index, snap_to_vein)
        if _len(_sub(e0, e1)) < 0.3:
            continue
        cross.append([grid.from_grid(e0), grid.from_grid(e1)])
    centres_w = [grid.from_grid(c) for c in centres]
    return cross, centres_w


def _gidx(grid, g):
    i = min(max(int(round(g[0])), 0), grid.nx - 1)
    j = min(max(int(round(g[1])), 0), grid.ny - 1)
    return j * grid.nx + i


def _snap_end(e, cl_index, snap_to_vein):
    best, bd = None, 2.6
    for c in cl_index.query(e):
        d = _len(_sub(c, e))
        if d < bd:
            bd, best = d, c
    if best is not None:
        return best
    s = snap_to_vein(e, 2.6)
    return s if s is not None else e


def _cluster_points(items, radius):
    """Greedy union of nearby points.  items: [(pt, flag)] -> [(centroid, any_flag)]"""
    cell = {}
    parent = list(range(len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for idx, (pt, _f) in enumerate(items):
        key = (int(pt[0] // radius), int(pt[1] // radius))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for o in cell.get((key[0] + dx, key[1] + dy), ()):
                    if _len(_sub(items[o][0], pt)) <= radius:
                        ra, rb = find(o), find(idx)
                        if ra != rb:
                            parent[ra] = rb
        cell.setdefault(key, []).append(idx)
    groups = {}
    for idx in range(len(items)):
        groups.setdefault(find(idx), []).append(idx)
    out = []
    for g in groups.values():
        sx = sum(items[i][0][0] for i in g) / len(g)
        sy = sum(items[i][0][1] for i in g) / len(g)
        out.append(((sx, sy), any(items[i][1] for i in g)))
    return out


class _PointIndex:
    def __init__(self, pts, cell):
        self.cell = cell
        self.map = {}
        for p in pts:
            self.map.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(p)

    def query(self, p):
        kx, ky = int(p[0] // self.cell), int(p[1] // self.cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for q in self.map.get((kx + dx, ky + dy), ()):
                    yield q


class _SegmentIndex:
    def __init__(self, segs, cell):
        self.cell = cell
        self.map = {}
        for s in segs:
            a, b = s
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for kx in range(int(x0 // cell), int(x1 // cell) + 1):
                for ky in range(int(y0 // cell), int(y1 // cell) + 1):
                    self.map.setdefault((kx, ky), []).append(s)

    def query(self, p):
        kx, ky = int(p[0] // self.cell), int(p[1] // self.cell)
        seen = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for s in self.map.get((kx + dx, ky + dy), ()):
                    if id(s) not in seen:
                        seen.add(id(s))
                        yield s


# ---------------------------------------------------------------------------
# Pterostigma
# ---------------------------------------------------------------------------

def _make_pterostigma(shape, p):
    L = p.length
    n = 8
    top, bot = [], []
    w = p.ptero_width * p.chord
    for k in range(n + 1):
        u = p.ptero_start + (p.ptero_end - p.ptero_start) * k / n
        y = shape.y_le(u)
        top.append((u * L, y))
        bot.append((u * L, y - min(w, shape.width(u) * 0.5)))
    return top + list(reversed(bot))


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

class Venation:
    """Result container."""

    def __init__(self):
        self.outline = []
        self.veins = []          # list of Vein (incl. costa / margin / cross)
        self.centres = []
        self.pterostigma = None
        self.shape = None


def generate(params):
    p = params
    rng = random.Random(p.seed)
    shape = WingShape(p)
    outline, tip_idx = shape.outline()
    margin = Margin(outline)

    primaries = _make_primary_veins(shape, margin, p, rng)
    intercalaries = _make_intercalaries(primaries, margin, p)
    longis = primaries + intercalaries

    grid = Grid(outline, p)
    cross_lines, centres = _make_crossveins(grid, longis, outline, p, rng)

    res = Venation()
    res.shape = shape
    res.outline = outline
    # margin split into costa (leading edge) and the rest
    le = outline[:tip_idx + 1]
    rest = outline[tip_idx:] + [outline[0]]
    res.veins.append(Vein(le, "costa", 0))
    res.veins.append(Vein(rest, "margin", 3))
    res.veins.extend(longis)
    for c in cross_lines:
        res.veins.append(Vein(c, "cross", 4))
    res.centres = centres
    if p.pterostigma:
        res.pterostigma = _make_pterostigma(shape, p)
    return res


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESETS = {
    "DRAGONFLY_FORE": dict(chord=0.20, base_power=0.45, hinge=0.05, vein_curvature=0.3, tip_power=0.45,
                           tip_drop=0.20, le_bulge=0.03, n_primary=8,
                           subcosta_end=0.48, radius_end=0.95, last_vein_end=0.10,
                           branch_prob=0.5, intercalary_levels=2,
                           intercalary_threshold=0.08, 
                           pterostigma=True),
    "DRAGONFLY_HIND": dict(chord=0.30, base_power=0.16, tip_power=0.55,
                           tip_drop=0.25, le_bulge=0.02, n_primary=10,
                           subcosta_end=0.42, radius_end=0.95, last_vein_end=0.04,
                           branch_prob=0.55, intercalary_levels=2,
                           intercalary_threshold=0.07, 
                           pterostigma=True),
    "DAMSELFLY": dict(chord=0.15, base_power=0.9, tip_power=0.4,
                      tip_drop=0.25, le_bulge=0.02, n_primary=6,
                      subcosta_end=0.25, radius_end=0.95, last_vein_end=0.22,
                      branch_prob=0.3, intercalary_levels=1,
                      intercalary_threshold=0.15, 
                      pterostigma=True,
                      ptero_start=0.86, ptero_end=0.91),
    "LACEWING": dict(chord=0.32, base_power=0.30, tip_power=0.65,
                     tip_drop=0.45, le_bulge=0.06, n_primary=9,
                     subcosta_end=0.85, radius_end=0.96, last_vein_end=0.15,
                     branch_prob=0.75, intercalary_levels=1,
                     intercalary_threshold=0.12, 
                     pterostigma=False),
    "MAYFLY": dict(chord=0.42, base_power=0.22, tip_power=0.75,
                   tip_drop=0.10, le_bulge=0.02, n_primary=9, hinge=0.04,
                   subcosta_end=0.80, radius_end=0.97, last_vein_end=0.08,
                   vein_curvature=0.25, branch_prob=0.6, intercalary_levels=2,
                   intercalary_threshold=0.09, intercalary_stop=0.6,
                   cell_size=0.05, ladder_ratio=1.2, cell_noise=0.15,
                   pterostigma=False),
}
