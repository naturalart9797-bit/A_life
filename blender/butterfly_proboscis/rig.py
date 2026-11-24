# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bone rig for the butterfly proboscis.

Generate Rig turns the procedural (Geometry Nodes) proboscis into
  * a real mesh (all quads, auto UV) skinned to
  * a chain of deform bones (shorter towards the tip, max ~15 deg bend each)
  * inside a new or an existing armature (e.g. the butterfly body rig),
    parented to the head bone so it follows the head automatically.

One slider on the control bone drives everything (driver "simple expressions",
so no Python auto-run is needed and the animation bakes/exports normally):

  extend  0 = coiled (rest shape) ... 1 = uncoiled, tip on the target
  aim     0 = extend to the default straight-ish shape, 1 = reach the target

Reaching: the extended proboscis has a fixed bend profile (a "knee" plus a
slight distal curve) scaled by one bend amount. Because the chain length is
fixed, tip distance depends only on that bend amount; the inverse
(distance -> bend, chord angle) is fitted with polynomials at build time.
Drivers then compute yaw / pitch / bend from the target position measured in
the head's space, so the tip lands on the target exactly and stays there
while the head moves.
"""

import math

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix, Quaternion, Vector

RIG_TAG = "proboscis_rig"
CTRL_PROPS = ("extend", "aim")
_POLY_DEGREE = 8
_MAX_EXPR = 255  # driver expressions are truncated beyond this


# --------------------------------------------------------------------------
# Shape math (mirrors the node group exactly)
# --------------------------------------------------------------------------

def _smoothstep(x, a, b):
    if b == a:
        return 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class ShapeParams:
    NAMES = ("Length", "Turns", "Inner Radius", "Straight Base", "Base Angle",
             "Extended Bend", "Uncoil Softness", "Resolution")

    def __init__(self, mod):
        from . import get_input
        v = {n: get_input(mod, n) for n in self.NAMES}
        self.length = max(float(v["Length"]), 1.0e-4)
        self.turns = max(float(v["Turns"]), 0.01)
        self.r_in = max(float(v["Inner Radius"]), 1.0e-5)
        self.base = float(v["Straight Base"])
        self.base_angle = float(v["Base Angle"])
        self.bend = float(v["Extended Bend"])
        self.soft = max(float(v["Uncoil Softness"]), 0.001)
        self.n = int(v["Resolution"])

    def theta(self, u, base_angle, bend):
        """Angle of each GN segment (segment j = point j -> j+1), as the nodes."""
        L, N = self.length, self.n
        ds = L / (N - 1)
        lb = self.base * L
        lc = max(L - lb, 1.0e-4)
        r0 = lc / (self.turns * math.pi) - self.r_in
        g = max((r0 - self.r_in) / self.turns, 1.0e-6)
        w = self.soft
        front = u * (1 + 2 * w) - w
        out, acc = [], 0.0
        for i in range(N):
            s = i / (N - 1)
            la = s * L
            ell = max(la - lb, 0.0)
            r_sq = max(r0 * r0 - g / math.pi * ell, self.r_in * self.r_in)
            k_coil = _smoothstep(la, lb * 0.4, lb + 1.0e-4) / math.sqrt(r_sq)
            k_ext = bend / L
            c = _smoothstep(s, front - w, front + w)
            k = k_ext + c * (k_coil - k_ext)
            out.append(base_angle + acc)
            acc += k * ds
        return out, ds

    @staticmethod
    def seg_theta(th, ds, l):
        return th[min(max(int(l / ds), 0), len(th) - 2)]

    def chord_angle(self, th, ds, a, b, n=64):
        cy = cz = 0.0
        for j in range(n):
            t = self.seg_theta(th, ds, a + (b - a) * (j + 0.5) / n)
            cy += math.cos(t)
            cz += math.sin(t)
        return math.atan2(cz, cy)

    def bone_bounds(self, max_angle, max_len_frac=1.0 / 32.0, count=0, equal=False):
        """Split the length so no bone bends more than max_angle when coiled
        (or into exactly `count` bones)."""
        L = self.length
        th, ds = self.theta(0.0, 0.0, 0.0)
        if equal:
            arc = [i * ds for i in range(len(th))]
            n = max(int(count), 4) if count else auto_equal_count(arc, th, max_angle)
            return [L * i / n for i in range(n + 1)]
        if count:
            arc = [i * ds for i in range(len(th))]
            return bounds_by_count(arc, th, count)

        def at(l):
            i = min(max(l / ds, 0.0), len(th) - 1.0)
            a = int(i)
            b = min(a + 1, len(th) - 1)
            return th[a] + (th[b] - th[a]) * (i - a)

        bounds, start, l = [0.0], 0.0, 0.0
        step, max_len = L / 4000.0, L * max_len_frac
        while l < L:
            l += step
            if abs(at(l) - at(start)) >= max_angle or l - start >= max_len:
                bounds.append(min(l, L))
                start = l
        if L - bounds[-1] < 1.0e-6:
            bounds[-1] = L
        else:
            bounds.append(L)
        return bounds

    def relative_angles(self, bounds, u, base_angle, bend):
        th, ds = self.theta(u, base_angle, bend)
        out, prev = [], 0.0
        for i in range(len(bounds) - 1):
            phi = self.chord_angle(th, ds, bounds[i], bounds[i + 1])
            out.append(math.remainder(phi - prev, math.tau))
            prev = phi
        return out


def _bend_profile(mids, L, knee, knee_width, knee_share):
    """Cumulative bend fraction (0..1) at each bone middle."""
    out = []
    for m in mids:
        s = m / L
        k = _smoothstep(s, knee - knee_width / 2, knee + knee_width / 2)
        d = max(s - knee, 0.0) / max(1.0 - knee, 1.0e-6)
        out.append(knee_share * k + (1.0 - knee_share) * d)
    return out


def _fit_reach(lens, profile, L, max_bend):
    """Fit bend(t) and chord(t), t = sqrt(1 - dist/L), for the given chain."""
    import numpy as np

    bends = np.linspace(0.0, max_bend, 2000)
    tips = []
    for b in bends:
        f = d = 0.0
        for ln, w in zip(lens, profile):
            f += ln * math.cos(b * w)
            d += ln * math.sin(b * w)
        tips.append((f, d))
    tips = np.array(tips)
    dist = np.hypot(tips[:, 0], tips[:, 1])
    chord = np.arctan2(tips[:, 1], tips[:, 0])
    # keep the monotonic part (distance must shrink as the bend grows)
    stop = len(dist)
    for i in range(1, len(dist)):
        if dist[i] >= dist[i - 1]:
            stop = i
            break
    bends, dist, chord = bends[:stop], dist[:stop], chord[:stop]
    t = np.sqrt(np.clip(1.0 - dist / L, 0.0, 1.0))
    c_bend = np.polyfit(t, bends, _POLY_DEGREE)
    c_chord = np.polyfit(t, chord, _POLY_DEGREE)
    err = max(float(np.abs(np.polyval(c_bend, t) - bends).max()),
              float(np.abs(np.polyval(c_chord, t) - chord).max()))
    return [float(c) for c in c_bend], [float(c) for c in c_chord], float(dist.min() / L), err


def _horner(coeffs, var="t"):
    expr = f"{coeffs[0]:.6g}"
    for c in coeffs[1:]:
        expr = f"({expr}) * {var} + {c:.6g}"
    return expr


# --------------------------------------------------------------------------
# Driver helpers
# --------------------------------------------------------------------------

def _add_driver(arm, path, index, expression, prop_vars=(), loc_bone=None, ctrl=None):
    fcurve = arm.driver_add(path, index) if index is not None else arm.driver_add(path)
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    for v in list(drv.variables):
        drv.variables.remove(v)
    if loc_bone:
        for name, axis in (("x", "LOC_X"), ("y", "LOC_Y"), ("z", "LOC_Z")):
            var = drv.variables.new()
            var.name = name
            var.type = "TRANSFORMS"
            tgt = var.targets[0]
            tgt.id = arm
            tgt.bone_target = loc_bone
            tgt.transform_type = axis
            tgt.transform_space = "LOCAL_SPACE"
    for name, prop in prop_vars:
        var = drv.variables.new()
        var.name = name
        var.type = "SINGLE_PROP"
        var.targets[0].id = arm
        var.targets[0].data_path = f'pose.bones["{ctrl}"]["{prop}"]'
    drv.expression = expression
    if len(expression) >= _MAX_EXPR or not drv.is_simple_expression:
        raise RuntimeError(f"driver expression not usable: {expression}")
    return drv


def _set_prop(pbone, name, value, description="", min_value=None, max_value=None):
    pbone[name] = value
    try:
        ui = pbone.id_properties_ui(name)
        kw = {"description": description}
        if min_value is not None:
            kw.update(min=min_value, soft_min=min_value)
        if max_value is not None:
            kw.update(max=max_value, soft_max=max_value)
        ui.update(**kw)
    except (AttributeError, TypeError):
        pass


def _bone_collection(arm_data, name, visible=True):
    colls = getattr(arm_data, "collections", None)
    if colls is None:
        return None
    coll = colls.get(name) or colls.new(name)
    coll.is_visible = visible
    return coll


# --------------------------------------------------------------------------
# Rig lookup (used by the UI)
# --------------------------------------------------------------------------

def find_rig_controls(obj):
    """Return (armature object, control pose bone) for a proboscis rig."""
    if obj is None:
        return None, None
    arm = None
    if obj.type == "ARMATURE":
        arm = obj
    elif obj.type == "MESH":
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is not None:
                arm = mod.object
                break
    if arm is None or arm.pose is None:
        return None, None
    for pb in arm.pose.bones:
        if pb.get(RIG_TAG):
            return arm, pb
    return arm, None


def rig_target_constraint(arm, ctrl):
    aim = arm.pose.bones.get(ctrl.get("aim_bone", ""))
    if aim is None:
        return None
    return next((c for c in aim.constraints if c.type == "COPY_LOCATION"), None)


# --------------------------------------------------------------------------
# Centerline of an existing tube mesh
# --------------------------------------------------------------------------

def _dijkstra(adj, seeds, n):
    import heapq
    dist = [math.inf] * n
    heap = []
    for v in seeds:
        dist[v] = 0.0
        heap.append((0.0, v))
    heapq.heapify(heap)
    while heap:
        d, u = heapq.heappop(heap)
        if d > dist[u]:
            continue
        for v, w in adj[u]:
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return dist


def _project_on_polyline(co, pts, arc, guess, window):
    """Arc position of the closest point on pts[guess-window .. guess+window]."""
    import numpy as np
    lo = max(int(guess) - window, 0)
    hi = min(int(guess) + window, len(pts) - 1)
    a = pts[lo:hi]
    b = pts[lo + 1:hi + 1]
    ab = b - a
    t = np.clip(np.einsum("ij,ij->i", co - a, ab) / np.maximum(np.einsum("ij,ij->i", ab, ab), 1e-20), 0, 1)
    proj = a + ab * t[:, None]
    k = int(np.argmin(np.einsum("ij,ij->i", proj - co, proj - co)))
    return arc[lo + k] + t[k] * (arc[lo + k + 1] - arc[lo + k])


def _smooth_polyline(pts, radius=2):
    import numpy as np
    sm = pts.copy()
    for i in range(1, len(pts) - 1):
        lo, hi = max(i - radius, 0), min(i + radius + 1, len(pts))
        sm[i] = pts[lo:hi].mean(axis=0)
    return sm


def _arc(pts):
    import numpy as np
    seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return np.concatenate([[0.0], np.cumsum(seg)])


def _ls_centers(co, nr, sval, idx, nbins):
    """Per bin of sval: least-squares tube center c and radius r with
    co_v ~= c + r * n_v (uses normals, so partial rings do not bias c)."""
    import numpy as np
    smax = float(sval[idx].max())
    b = np.clip((sval[idx] / smax * nbins).astype(int), 0, nbins - 1)
    centers, radii, svals = [], [], []
    for k in range(nbins):
        m = idx[b == k]
        if len(m) < 3:
            continue
        P, N = co[m], nr[m]
        A = np.zeros((4, 4))
        A[:3, :3] = np.eye(3) * len(m)
        A[:3, 3] = A[3, :3] = N.sum(axis=0)
        A[3, 3] = len(m)
        rhs = np.concatenate([P.sum(axis=0), [np.einsum("ij,ij->", N, P)]])
        if np.linalg.cond(A) > 1e8:
            continue
        x = np.linalg.solve(A, rhs)
        centers.append(x[:3])
        radii.append(x[3])
        svals.append(float(sval[m].mean()))
    return np.array(centers), np.array(radii), np.array(svals)


def tube_centerline(coords, normals, edges, flip=False):
    """Centerline of a (coiled) tube mesh.

    Returns (points, arc, per-vertex arc position, plane normal, radius).
    The base is the end farther from the middle of the coil (outer end).
    """
    import numpy as np
    co = np.array(coords, dtype=float)
    nr = np.array(normals, dtype=float)
    nr /= np.maximum(np.linalg.norm(nr, axis=1), 1e-12)[:, None]
    n = len(co)
    adj = [[] for _ in range(n)]
    elen = []
    for a, b in edges:
        w = float(np.linalg.norm(co[a] - co[b]))
        elen.append(w)
        adj[a].append((b, w))
        adj[b].append((a, w))
    h = float(np.median(elen)) if elen else 1.0
    # largest connected part
    label = [-1] * n
    comps = []
    for v0 in range(n):
        if label[v0] >= 0:
            continue
        stack, members = [v0], []
        label[v0] = len(comps)
        while stack:
            u = stack.pop()
            members.append(u)
            for v, _ in adj[u]:
                if label[v] < 0:
                    label[v] = label[v0]
                    stack.append(v)
        comps.append(members)
    main = np.array(max(comps, key=len))

    # the two ends of the tube: double sweep of surface distances
    d0 = np.array(_dijkstra(adj, [int(main[0])], n))
    end_a = int(main[np.argmax(d0[main])])
    da = np.array(_dijkstra(adj, [end_a], n))
    end_b = int(main[np.argmax(da[main])])
    middle = co[main].mean(axis=0)
    base = end_a
    if np.linalg.norm(co[end_a] - middle) < np.linalg.norm(co[end_b] - middle):
        base = end_b
    if flip:
        base = end_b if base == end_a else end_a
    dist = da if base == end_a else np.array(_dijkstra(adj, [base], n))

    # pass 1: coarse centers from surface distance (bins ~3 rings wide)
    total = float(dist[main].max())
    coarse = int(min(max(total / (3.0 * h), 20), 200))
    c1, r1, s1 = _ls_centers(co, nr, dist, main, coarse)
    c1 = _smooth_polyline(c1, 1)
    arc1 = _arc(c1)
    radius = float(np.median(np.abs(r1)))
    window = int(math.ceil(4.0 * radius / max(arc1[-1] / max(len(c1) - 1, 1), 1e-12))) + 3

    def project_all(pts, arc, guess_of):
        out = np.zeros(n)
        for v in main:
            out[v] = _project_on_polyline(co[v], pts, arc, guess_of(v), window)
        return out

    sval = project_all(c1, arc1, lambda v: np.interp(dist[v], s1, np.arange(len(s1))))

    # pass 2: every vertex points at the axis: center_v = co_v - r(s) * n_v
    r_of = np.interp(sval, arc1, _smooth_polyline(r1[:, None], 2)[:, 0])
    tang1 = np.gradient(c1, axis=0)
    tang1 /= np.maximum(np.linalg.norm(tang1, axis=1), 1e-12)[:, None]
    seg_idx = np.clip(np.searchsorted(arc1, sval) - 1, 0, len(c1) - 1)
    along = np.abs(np.einsum("ij,ij->i", nr, tang1[seg_idx]))
    use = np.array([v for v in main if along[v] < 0.6])       # skip end caps
    cv = co - nr * r_of[:, None]
    fine = int(min(max(arc1[-1] / h, 50), 600))
    order = np.clip((sval[use] / max(sval[use].max(), 1e-12) * fine).astype(int), 0, fine - 1)
    pts, ss = [], []
    for k in range(fine):
        m = use[order == k]
        if len(m):
            pts.append(cv[m].mean(axis=0))
            ss.append(sval[m].mean())
    pts = np.array(pts)
    ss = np.array(ss)
    # drop the samples next to the end caps (cap / tip-hole shapes disturb them)
    s_lo, s_hi = float(sval[main].min()), float(sval[main].max())
    keep = (ss > s_lo + 1.0 * radius) & (ss < s_hi - 1.0 * radius)
    if keep.sum() >= 8:
        pts, ss = pts[keep], ss[keep]
    pts = _smooth_polyline(pts, 3)
    ss = list(ss)
    # extend straight to the real ends of the tube: the middle of the end caps
    def cap_center(sel):
        m = main[sel]
        return co[m].mean(axis=0) if len(m) else None

    s_main = sval[main]
    ends = []
    for sel, anchor, s_end in ((s_main < s_lo + 0.5 * radius, pts[0], s_lo),
                               (s_main > s_hi - 0.5 * radius, pts[-1], s_hi)):
        c_end = cap_center(sel)
        ends.append(c_end if c_end is not None else anchor)
    steps = 6

    def bridge(p_from, p_prev, p_to):
        """Quadratic Bezier from p_from to p_to, tangent-continuous at p_from."""
        t = p_from - p_prev
        t = t / max(np.linalg.norm(t), 1e-12)
        ctrl = p_from + t * (np.linalg.norm(p_to - p_from) * 0.5)
        out = []
        for i in range(1, steps + 1):
            u = i / steps
            out.append((1 - u) ** 2 * p_from + 2 * (1 - u) * u * ctrl + u * u * p_to)
        return out

    k = min(3, len(pts) - 1)
    head = bridge(pts[0], pts[k], ends[0])[::-1]
    tail = bridge(pts[-1], pts[-1 - k], ends[1])
    pts = np.vstack([np.array(head), pts, np.array(tail)])
    ss = ([s_lo + (ss[0] - s_lo) * i / steps for i in range(steps)] + ss
          + [ss[-1] + (s_hi - ss[-1]) * (i + 1) / steps for i in range(steps)])
    arc = _arc(pts)
    sval = project_all(pts, arc, lambda v: np.interp(sval[v], ss, np.arange(len(pts))))

    # vertices of other loose parts take the value of their nearest vertex
    if len(main) < n:
        from mathutils import kdtree
        kd = kdtree.KDTree(len(main))
        for v in main:
            kd.insert(co[v], int(v))
        kd.balance()
        in_main = np.zeros(n, dtype=bool)
        in_main[main] = True
        for v in range(n):
            if not in_main[v]:
                sval[v] = sval[kd.find(co[v])[1]]

    # plane of the coil, oriented so the coil turns positively about it
    c = pts - pts.mean(axis=0)
    normal = np.linalg.eigh(c.T @ c)[1][:, 0]
    tang = np.diff(pts, axis=0)
    tang /= np.maximum(np.linalg.norm(tang, axis=1), 1e-12)[:, None]
    turn = sum(math.atan2(float(np.dot(normal, np.cross(tang[i - 1], tang[i]))),
                          float(np.dot(tang[i - 1], tang[i]))) for i in range(1, len(tang)))
    if turn < 0:
        normal = -normal
    return pts, arc, sval, normal, radius


def _signed_angle(a, b, axis):
    return math.atan2(axis.dot(a.cross(b)), a.dot(b))


def bounds_by_count(arc, phi, count, max_bend=math.radians(15.0), max_len_frac=1.0 / 32.0):
    """Arc positions splitting the length into exactly `count` bones.

    Same distribution as the automatic split (short where the coil is tight,
    longer where it is straight): each bone gets an equal share of
    integral max(|turn| / max_bend, length / (L * max_len_frac)).
    """
    import numpy as np
    count = max(int(count), 4)
    arc = np.asarray(arc, dtype=float)
    phi = np.asarray(phi, dtype=float)
    L = float(arc[-1])
    cost = np.maximum(np.abs(np.diff(phi)) / max_bend, np.diff(arc) / (L * max_len_frac))
    acc = np.concatenate([[0.0], np.cumsum(cost)])
    return [float(x) for x in np.interp(np.linspace(0.0, acc[-1], count + 1), acc, arc)]


def _place_chords(P, length):
    """Walk the polyline placing joints at straight-line distance `length`.
    Returns (joints, arc positions, fractional bone count)."""
    import numpy as np
    seg_len = np.linalg.norm(np.diff(P, axis=0), axis=1)
    seg_arc = np.concatenate([[0.0], np.cumsum(seg_len)])
    joints, arcs = [P[0]], [0.0]
    cur, k = P[0], 0
    while True:
        hit = None
        for j in range(k, len(P) - 1):
            if np.linalg.norm(P[j + 1] - cur) >= length:
                d = P[j + 1] - P[j]
                f = P[j] - cur
                dd = float(d @ d)
                fd = float(f @ d)
                disc = max(fd * fd - dd * (float(f @ f) - length * length), 0.0)
                t = min(max((-fd + math.sqrt(disc)) / max(dd, 1e-20), 0.0), 1.0)
                hit = (P[j] + d * t, seg_arc[j] + seg_len[j] * t, j)
                break
        if hit is None:
            break
        cur, a, k = hit
        joints.append(cur)
        arcs.append(float(a))
    rest = float(np.linalg.norm(P[-1] - cur))
    return joints, arcs, len(joints) - 1 + rest / length


def equal_chord_chain(pts, count):
    """Exactly `count` bones of identical straight length along the polyline."""
    import numpy as np
    P = np.asarray(pts, dtype=float)
    total = float(np.linalg.norm(np.diff(P, axis=0), axis=1).sum())
    lo, hi = total / count * 0.2, total / count * 1.0001
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if _place_chords(P, mid)[2] > count:
            lo = mid
        else:
            hi = mid
    joints, arcs, _ = _place_chords(P, hi)
    joints, arcs = joints[:count], arcs[:count]
    joints.append(P[-1])
    arcs.append(total)
    return [Vector(j) for j in joints], arcs


def auto_equal_count(arc, phi, max_bend, cap=300):
    """Bones needed so equal-length bones bend <= max_bend at the tightest spot."""
    import numpy as np
    arc = np.asarray(arc, dtype=float)
    phi = np.asarray(phi, dtype=float)
    w = 3
    rates = [abs(phi[min(i + w, len(phi) - 1)] - phi[i]) / max(arc[min(i + w, len(arc) - 1)] - arc[i], 1e-12)
             for i in range(len(phi) - 1)]
    return int(min(max(math.ceil(float(arc[-1]) * max(rates) / max_bend), 4), cap))


def chain_from_centerline(pts, arc, normal, max_bend, max_len_frac=1.0 / 32.0, count=0,
                          equal=False):
    """Split the centerline into bones of at most max_bend turning each
    (or into exactly `count` bones)."""
    import numpy as np
    nrm = Vector(normal)
    P = [Vector(p) for p in pts]
    L = float(arc[-1])
    phi = [0.0]
    for k in range(1, len(P) - 1):
        phi.append(phi[-1] + _signed_angle(P[k] - P[k - 1], P[k + 1] - P[k], nrm))
    phi.append(phi[-1])
    if equal:
        n = max(int(count), 4) if count else auto_equal_count(arc, phi, max_bend)
        return equal_chord_chain(pts, n)
    if count:
        import numpy as np
        bounds = bounds_by_count(arc, phi, count)
        arr = np.array([tuple(p) for p in P])
        chain = [Vector([float(np.interp(b, arc, arr[:, j])) for j in range(3)]) for b in bounds]
        return chain, bounds
    cuts, start = [0], 0
    for k in range(1, len(P)):
        if (abs(phi[k] - phi[start]) >= max_bend or arc[k] - arc[start] >= L * max_len_frac):
            cuts.append(k)
            start = k
    if cuts[-1] != len(P) - 1:
        if arc[-1] - arc[cuts[-1]] < 0.25 * (arc[cuts[-1]] - arc[cuts[-2]]) and len(cuts) > 2:
            cuts[-1] = len(P) - 1
        else:
            cuts.append(len(P) - 1)
    chain = [P[k] for k in cuts]
    bounds = [float(arc[k]) for k in cuts]
    return chain, bounds


# --------------------------------------------------------------------------
# Shared rig builder
# --------------------------------------------------------------------------

def _rest_world_matrix(context, obj, arm):
    """World matrix of obj with the involved armatures in their rest pose."""
    rigs = {a for a in (arm, obj.parent) if a is not None and a.type == "ARMATURE"}
    states = [(a, a.data.pose_position) for a in rigs]
    for a, _ in states:
        a.data.pose_position = "REST"
    context.view_layer.update()
    mat = obj.matrix_world.copy()
    for a, st in states:
        a.data.pose_position = st
    context.view_layer.update()
    return mat


def _skin(mesh_obj, names, svals, mids):
    nb = len(names)
    for n in names:
        old = mesh_obj.vertex_groups.get(n)
        if old is not None:
            mesh_obj.vertex_groups.remove(old)
    groups = [mesh_obj.vertex_groups.new(name=n) for n in names]
    for vi, y in enumerate(svals):
        if y <= mids[0]:
            groups[0].add([vi], 1.0, "REPLACE")
        elif y >= mids[-1]:
            groups[-1].add([vi], 1.0, "REPLACE")
        else:
            j = next(j for j in range(nb - 1) if y < mids[j + 1])
            t = (y - mids[j]) / (mids[j + 1] - mids[j])
            groups[j].add([vi], 1.0 - t, "REPLACE")
            groups[j + 1].add([vi], t, "REPLACE")
    if not any(m.type == "ARMATURE" for m in mesh_obj.modifiers):
        mesh_obj.modifiers.new("Armature", "ARMATURE")


def build_rig(context, arm, parent_bone, prefix, chain, bend_axis, rest, coil, ext,
              sign, soft, knee, knee_width, knee_share, create_target):
    """Create bones, controls, target and drivers.

    chain      armature-space joint positions (n_bones + 1)
    bend_axis  armature-space axis the chain bends around (becomes bone X)
    rest/coil/ext  relative angle per bone (bone 0 relative to its rest frame)
               in the rest pose / coiled / default extended
    sign       +1: positive angle = rotation about +X, -1: about -X
    Returns (bone names, control bone name, fit error).
    """
    nb = len(chain) - 1
    lens = [(chain[i + 1] - chain[i]).length for i in range(nb)]
    L = sum(lens)
    bounds = [0.0]
    for ln in lens:
        bounds.append(bounds[-1] + ln)
    mids = [(bounds[i] + bounds[i + 1]) / 2 for i in range(nb)]
    profile = _bend_profile(mids, L, knee, knee_width, knee_share)
    c_bend, c_chord, r_min, fit_err = _fit_reach(lens, profile, L, math.radians(170.0))

    axis = Vector(bend_axis).normalized()
    y0 = (chain[1] - chain[0]).normalized()
    curl = sign * axis.cross(y0)          # side the coil / knee bends to
    names = [f"{prefix}.{i:03d}" for i in range(nb)]
    ctrl_name = f"{prefix}_ctrl"
    aim_name = f"MCH-{prefix}_aim"

    for o in context.selected_objects:
        o.select_set(False)
    arm.hide_set(False)
    arm.select_set(True)
    context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    ebones = arm.data.edit_bones
    parent = ebones.get(parent_bone) if parent_bone else None
    deform_coll = _bone_collection(arm.data, "Proboscis")
    ctrl_coll = _bone_collection(arm.data, "Proboscis Controls")
    mch_coll = _bone_collection(arm.data, "Proboscis MCH", visible=False)

    def roll(eb):
        y = (eb.tail - eb.head).normalized()
        eb.align_roll(axis.cross(y))       # bone X = bend axis

    prev = parent
    for i, name in enumerate(names):
        eb = ebones.new(name)
        eb.head, eb.tail = chain[i], chain[i + 1]
        roll(eb)
        eb.parent = prev
        eb.use_connect = i > 0
        eb.use_deform = True
        if deform_coll:
            deform_coll.assign(eb)
        prev = eb
    aim = ebones.new(aim_name)
    aim.head, aim.tail = chain[0], chain[1]
    roll(aim)
    aim.parent = parent
    aim.use_deform = False
    if mch_coll:
        mch_coll.assign(aim)
    ctrl = ebones.new(ctrl_name)
    ctrl.head = chain[0] - curl * (0.15 * L) - y0 * (0.05 * L)
    ctrl.tail = ctrl.head + y0 * (0.15 * L)
    roll(ctrl)
    ctrl.parent = parent
    ctrl.use_deform = False
    if ctrl_coll:
        ctrl_coll.assign(ctrl)
    bpy.ops.object.mode_set(mode="OBJECT")

    pbones = arm.pose.bones
    cpb = pbones[ctrl_name]
    _set_prop(cpb, RIG_TAG, True)
    _set_prop(cpb, "aim_bone", aim_name)
    _set_prop(cpb, "root_bone", names[0])
    _set_prop(cpb, "extend", 0.0, "0 = 巻いた状態 / 1 = 伸ばして目標に届く", 0.0, 1.0)
    _set_prop(cpb, "aim", 1.0, "1 = 目標に向かって伸ばす / 0 = 決まった形に伸ばす", 0.0, 1.0)
    for p in ("_yaw", "_beta", "_tq", "_bend", "_alpha"):
        cpb[p] = 0.0
    cpb["reach_length"] = L
    cpb["reach_min"] = r_min
    cpb.lock_location = cpb.lock_rotation = cpb.lock_scale = (True, True, True)

    target = None
    if create_target:
        target = bpy.data.objects.new(f"{prefix}_target", None)
        target.empty_display_type = "SPHERE"
        target.empty_display_size = 0.03 * L * arm.matrix_world.to_scale().x
        context.collection.objects.link(target)
        ang, pos = 0.0, Vector(chain[0])
        for ln, d in zip(lens, ext):
            ang += d
            pos = pos + (y0 * math.cos(ang) + curl * math.sin(ang)) * ln
        target.location = arm.matrix_world @ pos
    con = pbones[aim_name].constraints.new("COPY_LOCATION")
    con.name = "Proboscis Target"
    con.target = target

    C = ctrl_name
    zc = "-z" if sign < 0 else "z"
    _add_driver(arm, f'pose.bones["{C}"]["_yaw"]', None, "atan2(-x, y)",
                loc_bone=aim_name, ctrl=C)
    _add_driver(arm, f'pose.bones["{C}"]["_beta"]', None, f"atan2({zc}, sqrt(x*x + y*y))",
                loc_bone=aim_name, ctrl=C)
    _add_driver(arm, f'pose.bones["{C}"]["_tq"]', None,
                f"sqrt(1 - clamp(sqrt(x*x + y*y + z*z) / {L:.6g}, {r_min + 1e-4:.6f}, 1))",
                loc_bone=aim_name, ctrl=C)
    _add_driver(arm, f'pose.bones["{C}"]["_bend"]', None, _horner(c_bend),
                [("t", "_tq")], ctrl=C)
    _add_driver(arm, f'pose.bones["{C}"]["_alpha"]', None, f"b - ({_horner(c_chord)})",
                [("t", "_tq"), ("b", "_beta")], ctrl=C)

    w = soft
    lead = "-" if sign < 0 else ""
    bone_vars = [("e", "extend"), ("m", "aim"), ("g", "_bend"), ("a", "_alpha")]
    for i, name in enumerate(names):
        pb = pbones[name]
        pb.rotation_mode = "XYZ"
        pb.lock_location = (True, True, True)
        coiled = (f"smoothstep(e * {1 + 2 * w:.4f} - {2 * w:.4f}, e * {1 + 2 * w:.4f}, "
                  f"{mids[i] / L:.5f})")
        reach = (f"a + g * {profile[0]:.6f}" if i == 0
                 else f"g * {profile[i] - profile[i - 1]:.6f}")
        body = f"lerp(lerp({ext[i]:.6f}, {reach}, m), {coil[i]:.6f}, {coiled})"
        if abs(rest[i]) > 1e-9:
            body = f"{body} - {rest[i]:.6f}"
        _add_driver(arm, f'pose.bones["{name}"].rotation_euler', 0, f"{lead}({body})",
                    bone_vars, ctrl=C)
    _add_driver(arm, f'pose.bones["{names[0]}"].rotation_euler', 2,
                "w * m * smoothstep(0, 1, e)",
                [("e", "extend"), ("m", "aim"), ("w", "_yaw")], ctrl=C)
    return names, ctrl_name, mids, fit_err


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class _RigOptions:
    armature_name: StringProperty(
        name="Armature",
        description="追加先のアーマチュア（蝶の本体のリグ）。空なら新しく作ります")
    parent_bone: StringProperty(
        name="Head Bone", description="口吻の親にするボーン（頭）")
    prefix: StringProperty(name="Bone Name", default="proboscis",
                           description="作るボーンの名前の頭")
    bone_count: IntProperty(name="Bone Count", default=0, min=0, max=500,
                            description="ボーンの本数。0 = 自動（Max Bend per Bone で決める）。"
                                        "数を指定しても先端ほど短く配置します")
    equal_length: BoolProperty(name="Equal Bone Length", default=False,
                               description="ボーンを全部同じ長さにする（オフ = 先端ほど短く）")
    max_bend: FloatProperty(name="Max Bend per Bone", default=math.radians(15.0),
                            min=math.radians(3.0), max=math.radians(45.0),
                            subtype="ANGLE",
                            description="巻いたとき 1 本のボーンが曲がる最大角度"
                                        "（小さいほどなめらか・本数が増える）")
    knee: FloatProperty(name="Knee Position", default=0.45, min=0.15, max=0.8,
                        subtype="FACTOR",
                        description="伸ばしたときに曲がる「ひざ」の位置（根元から）")
    knee_width: FloatProperty(name="Knee Width", default=0.15, min=0.02, max=0.5,
                              subtype="FACTOR", description="ひざの曲がりの幅")
    knee_share: FloatProperty(name="Knee Share", default=0.85, min=0.0, max=1.0,
                              subtype="FACTOR",
                              description="曲がりのうち、ひざが受け持つ割合（残りは先端側のゆるいカーブ）")
    create_target: BoolProperty(name="Create Target", default=True,
                                description="伸ばした先の目標（Empty）を作る")

    def prefill(self, obj):
        par = obj.parent
        if par is not None and par.type == "ARMATURE" and not self.armature_name:
            self.armature_name = par.name
            if obj.parent_type == "BONE":
                self.parent_bone = obj.parent_bone

    def draw_common(self, layout):
        layout.prop_search(self, "armature_name", bpy.data, "objects")
        arm = bpy.data.objects.get(self.armature_name)
        if arm is not None and arm.type == "ARMATURE":
            layout.prop_search(self, "parent_bone", arm.data, "bones")
        layout.prop(self, "prefix")
        col = layout.column(align=True)
        col.prop(self, "equal_length")
        col.prop(self, "bone_count", text="Bone Count (0 = Auto)")
        sub = col.row()
        sub.enabled = self.bone_count == 0
        sub.prop(self, "max_bend")
        col = layout.column(align=True)
        col.prop(self, "knee")
        col.prop(self, "knee_width")
        col.prop(self, "knee_share")
        layout.prop(self, "create_target")

    def get_armature(self, context, world):
        """Existing armature (validated) or a new one. None + report on error."""
        if self.armature_name:
            arm = bpy.data.objects.get(self.armature_name)
            if arm is None or arm.type != "ARMATURE":
                self.report({"ERROR"}, f"アーマチュアが見つかりません: {self.armature_name}")
                return None
            if self.parent_bone and self.parent_bone not in arm.data.bones:
                self.report({"ERROR"}, f"ボーンが見つかりません: {self.parent_bone}")
                return None
            if any(b.name.startswith(self.prefix + ".") for b in arm.data.bones):
                self.report({"ERROR"}, f"'{self.prefix}' のボーンが既にあります。名前を変えてください")
                return None
            return arm
        data = bpy.data.armatures.new(f"{self.prefix}_rig")
        arm = bpy.data.objects.new(f"{self.prefix}_rig", data)
        context.collection.objects.link(arm)
        arm.matrix_world = Matrix.LocRotScale(*world.decompose()[:2], Vector((1, 1, 1)))
        arm.show_in_front = True
        return arm


def _finish(context, arm, mesh_obj, world):
    mesh_obj.parent = arm
    mesh_obj.parent_type = "OBJECT"
    mesh_obj.matrix_world = world
    for m in mesh_obj.modifiers:
        if m.type == "ARMATURE":
            m.object = arm
    for o in context.selected_objects:
        o.select_set(False)
    arm.select_set(True)
    context.view_layer.objects.active = arm
    context.view_layer.update()


class OBJECT_OT_proboscis_rig_mesh(_RigOptions, Operator):
    """自分で作った（巻いた形の）口吻メッシュにボーンを入れる。メッシュはそのまま"""
    bl_idname = "object.proboscis_rig_mesh"
    bl_label = "Rig This Mesh"
    bl_options = {"REGISTER", "UNDO"}

    flip: BoolProperty(name="Flip Base / Tip", default=False,
                       description="根元と先端の判定が逆のときにオン")
    extend_angle: FloatProperty(name="Extend Direction", default=0.0, subtype="ANGLE",
                                min=-math.pi, max=math.pi,
                                description="目標なしで伸ばすときの向き（根元の向きからの角度。正で巻く側）")
    extend_bend: FloatProperty(name="Extended Bend", default=math.radians(25.0),
                               subtype="ANGLE", min=-math.tau, max=math.tau,
                               description="目標なしで伸ばしたときの全体の反り")
    softness: FloatProperty(name="Uncoil Softness", default=0.15, min=0.01, max=1.0,
                            subtype="FACTOR", description="ほどける境目のなめらかさ")

    @classmethod
    def poll(cls, context):
        from . import find_proboscis_modifier
        obj = context.active_object
        return (obj is not None and obj.type == "MESH"
                and find_proboscis_modifier(obj) is None
                and find_rig_controls(obj)[1] is None)

    def invoke(self, context, event):
        self.prefill(context.active_object)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        self.draw_common(layout)
        layout.prop(self, "flip")
        col = layout.column(align=True)
        col.prop(self, "extend_angle")
        col.prop(self, "extend_bend")
        col.prop(self, "softness")

    def execute(self, context):
        obj = context.active_object
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        if len(obj.data.vertices) < 16:
            self.report({"ERROR"}, "メッシュが小さすぎます")
            return {"CANCELLED"}
        arm_pre = bpy.data.objects.get(self.armature_name) if self.armature_name else None
        world = _rest_world_matrix(context, obj, arm_pre)
        arm = self.get_armature(context, world)
        if arm is None:
            return {"CANCELLED"}
        to_arm = arm.matrix_world.inverted() @ world

        coords = [to_arm @ v.co for v in obj.data.vertices]
        nmat = to_arm.to_3x3().inverted().transposed()
        normals = [(nmat @ v.normal).normalized() for v in obj.data.vertices]
        edges = [tuple(e.vertices) for e in obj.data.edges]
        pts, arc, svals, normal, _radius = tube_centerline(coords, normals, edges, self.flip)
        chain, arc_bounds = chain_from_centerline(pts, arc, normal, self.max_bend,
                                                  count=self.bone_count,
                                                  equal=self.equal_length)
        nb = len(chain) - 1
        if nb < 4:
            self.report({"ERROR"}, "中心線を取れませんでした（筒状のメッシュですか？）")
            return {"CANCELLED"}
        axis = Vector(normal)
        dirs = [(chain[i + 1] - chain[i]).normalized() for i in range(nb)]
        rest = [0.0] + [_signed_angle(dirs[i - 1], dirs[i], axis) for i in range(1, nb)]
        lens = [(chain[i + 1] - chain[i]).length for i in range(nb)]
        L = sum(lens)
        mids, acc = [], 0.0
        for ln in lens:
            mids.append(acc + ln / 2)
            acc += ln
        k = self.extend_bend / L
        ext = [self.extend_angle + k * mids[0]] + [k * (mids[i] - mids[i - 1])
                                                   for i in range(1, nb)]

        names, _ctrl, _mids, fit_err = build_rig(
            context, arm, self.parent_bone, self.prefix, chain, axis, rest, rest, ext,
            +1, self.softness, self.knee, self.knee_width, self.knee_share,
            self.create_target)
        # weights: arc position of each vertex along the centerline vs. the
        # arc position of each bone's middle on the same centerline
        arc_mids = [(arc_bounds[i] + arc_bounds[i + 1]) / 2 for i in range(nb)]
        _skin(obj, names, [float(v) for v in svals], arc_mids)
        _finish(context, arm, obj, world)
        worst = math.degrees(max(abs(a) for a in rest[1:]))
        msg = f"{nb} bones / max bend per bone {worst:.0f} deg"
        if worst > 30:
            self.report({"WARNING"}, msg + "（巻きがカクカクします。本数を増やしてください）")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class OBJECT_OT_proboscis_generate_rig(_RigOptions, Operator):
    """アドオンで作った口吻からメッシュとリグを作る（頭のボーンの子・伸ばすと目標へ届く）"""
    bl_idname = "object.proboscis_generate_rig"
    bl_label = "Generate Rig"
    bl_options = {"REGISTER", "UNDO"}

    auto_uv: BoolProperty(name="Auto Seams + UV Unwrap", default=True)

    @classmethod
    def poll(cls, context):
        from . import find_proboscis_modifier
        return find_proboscis_modifier(context.active_object) is not None

    def invoke(self, context, event):
        self.prefill(context.active_object)
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.draw_common(self.layout)
        self.layout.prop(self, "auto_uv")

    def execute(self, context):
        from . import (find_proboscis_modifier, set_input, get_input,
                       _clean_material_slots, mark_proboscis_seams)
        src = context.active_object
        mod = find_proboscis_modifier(src)
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        arm_pre = bpy.data.objects.get(self.armature_name) if self.armature_name else None
        src_world = _rest_world_matrix(context, src, arm_pre)
        arm = self.get_armature(context, src_world)
        if arm is None:
            return {"CANCELLED"}

        params = ShapeParams(mod)
        bounds = params.bone_bounds(self.max_bend, count=self.bone_count,
                                    equal=self.equal_length)
        nb = len(bounds) - 1
        coil = params.relative_angles(bounds, 0.0, params.base_angle, params.bend)
        ext = params.relative_angles(bounds, 1.0, params.base_angle, params.bend)

        # straight rest mesh from the node group
        keep = {n: get_input(mod, n) for n in ("Uncoil", "Base Angle", "Extended Bend",
                                               "Wiggle Amount", "Use Edit Mesh")}
        for n, v in (("Uncoil", 1.0), ("Base Angle", 0.0), ("Extended Bend", 0.0),
                     ("Wiggle Amount", 0.0), ("Use Edit Mesh", False)):
            set_input(mod, n, v)
        src.update_tag()
        depsgraph = context.evaluated_depsgraph_get()
        depsgraph.update()
        mesh = bpy.data.meshes.new_from_object(src.evaluated_get(depsgraph),
                                               preserve_all_data_layers=True,
                                               depsgraph=depsgraph)
        for n, v in keep.items():
            set_input(mod, n, v)
        src.update_tag()
        mesh.name = f"{src.name}_rigged"
        _clean_material_slots(mesh)

        to_arm = arm.matrix_world.inverted() @ src_world
        chain = [to_arm @ Vector((0.0, b, 0.0)) for b in bounds]
        side = (to_arm.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
        names, _ctrl, _mids, fit_err = build_rig(
            context, arm, self.parent_bone, self.prefix, chain, side, [0.0] * nb, coil, ext,
            -1, params.soft, self.knee, self.knee_width, self.knee_share,
            self.create_target)

        mesh_obj = bpy.data.objects.new(f"{src.name}_mesh", mesh)
        context.collection.objects.link(mesh_obj)
        mids_local = [(bounds[i] + bounds[i + 1]) / 2 for i in range(nb)]
        _skin(mesh_obj, names, [v.co.y for v in mesh.vertices], mids_local)
        _finish(context, arm, mesh_obj, src_world)

        if self.auto_uv and mark_proboscis_seams(mesh):
            if not mesh.uv_layers:
                mesh.uv_layers.new(name="UVMap")
            arm.select_set(False)
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.002)
            bpy.ops.object.mode_set(mode="OBJECT")
            mesh_obj.select_set(False)
            arm.select_set(True)
            context.view_layer.objects.active = arm

        src.hide_set(True)
        src.hide_render = True
        worst = math.degrees(max(abs(a) for a in coil[1:]))
        msg = f"{nb} bones / max bend per bone {worst:.0f} deg"
        if worst > 30:
            self.report({"WARNING"}, msg + "（巻きがカクカクします。本数を増やしてください）")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}


class POSE_OT_proboscis_rig_drink(Operator):
    """伸ばして目標へ → 飲む → 巻き戻す のキーフレームを打つ（リグ用）"""
    bl_idname = "pose.proboscis_rig_drink"
    bl_label = "Add Drinking Keys"
    bl_options = {"REGISTER", "UNDO"}

    start_frame: IntProperty(name="Start Frame", default=1)
    extend_frames: IntProperty(name="Extend", default=24, min=1)
    drink_frames: IntProperty(name="Drink", default=60, min=0)
    retract_frames: IntProperty(name="Retract", default=36, min=1)

    @classmethod
    def poll(cls, context):
        return find_rig_controls(context.active_object)[1] is not None

    def invoke(self, context, event):
        self.start_frame = context.scene.frame_current
        return self.execute(context)

    def execute(self, context):
        arm, ctrl = find_rig_controls(context.active_object)
        path = f'pose.bones["{ctrl.name}"]["extend"]'
        f0 = self.start_frame
        f1 = f0 + self.extend_frames
        f2 = f1 + self.drink_frames
        f3 = f2 + self.retract_frames
        for frame, value in ((f0, 0.0), (f1, 1.0), (f2, 1.0), (f3, 0.0)):
            ctrl["extend"] = value
            arm.keyframe_insert(data_path=path, frame=frame)
        context.scene.frame_set(context.scene.frame_current)
        self.report({"INFO"}, f"frames {f0}-{f3}")
        return {"FINISHED"}


def draw_rig_panel(layout, context):
    """Panel section for a selected proboscis rig. Returns True if drawn."""
    arm, ctrl = find_rig_controls(context.active_object)
    if ctrl is None:
        return False
    box = layout.box()
    box.label(text=f"Proboscis Rig: {arm.name}", icon="ARMATURE_DATA")
    col = box.column(align=True)
    col.prop(ctrl, '["extend"]', text="Extend", slider=True)
    col.prop(ctrl, '["aim"]', text="Aim at Target", slider=True)
    con = rig_target_constraint(arm, ctrl)
    if con is not None:
        box.prop(con, "target", text="Target")
        root = arm.pose.bones.get(ctrl.get("root_bone", ""))
        if con.target is not None and root is not None:
            base = arm.matrix_world @ root.head
            ratio = (con.target.matrix_world.translation - base).length / max(
                ctrl.get("reach_length", 1.0) * arm.matrix_world.to_scale().x, 1e-9)
            icon = "CHECKMARK" if 0.55 <= ratio <= 0.95 else "ERROR"
            box.label(text=f"距離 / 長さ = {ratio:.2f}（0.55〜0.95 が自然）", icon=icon)
            if ratio > 1.0:
                box.label(text="届きません：頭を近づけるか目標を移動")
            elif ratio < ctrl.get("reach_min", 0.3) + 0.05:
                box.label(text="近すぎます：頭を離してください")
    box.label(text="Extend の上で I キー → キーフレーム", icon="KEYFRAME")
    box.operator(POSE_OT_proboscis_rig_drink.bl_idname, icon="MOD_FLUID")
    return True


classes = (
    OBJECT_OT_proboscis_rig_mesh,
    OBJECT_OT_proboscis_generate_rig,
    POSE_OT_proboscis_rig_drink,
)
