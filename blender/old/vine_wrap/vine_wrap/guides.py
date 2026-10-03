"""Guides: one curve object per vine guide (Bezier, smooth handles, target-local coords)."""

import math

import bpy
from mathutils import Vector
from mathutils.geometry import interpolate_bezier


def is_guide(obj):
    return obj is not None and obj.type == "CURVE" and obj.vine_guide.target is not None


def is_guide_of(obj, target):
    return obj is not None and obj.type == "CURVE" and target is not None and obj.vine_guide.target == target


def guide_objects(target):
    return [o for o in bpy.data.objects if is_guide_of(o, target)]


def guide_collection(context, target):
    name = target.name + "_VineGuides"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children and not any(
            coll.name in c.children for c in bpy.data.collections):
        context.scene.collection.children.link(coll)
    return coll


def style_of(P, obj):
    uid = obj.vine_guide.style_uid
    for s in P.styles:
        if s.uid == uid:
            return s
    return P.styles[0] if len(P.styles) else None


def ensure_style(P):
    if not len(P.styles):
        s = P.styles.add()
        s.name = "基本"
        s.uid = P.next_uid
        P.next_uid += 1
    return P.styles


def active_style(P):
    ensure_style(P)
    i = min(max(P.active_style_index, 0), len(P.styles) - 1)
    return P.styles[i]


def apply_display(obj, P):
    st = style_of(P, obj)
    if st is not None:
        obj.color = (*st.color, 1.0)


def new_guide(context, target, pts_world, radii=None, snap=True, name="Vine", auto=False):
    """Create a guide curve object from world-space points."""
    P = context.scene.vine_wrap
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 8
    obj = bpy.data.objects.new(name, cu)
    gs = obj.vine_guide
    gs.target = target
    gs.snap = snap
    gs.auto = auto
    gs.style_uid = active_style(P).uid
    guide_collection(context, target).objects.link(obj)
    obj.parent = target
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()
    # matrix_world is refreshed lazily; set it so the points land in the right place now.
    obj.matrix_world = target.matrix_world.copy()
    obj.show_in_front = True
    obj.hide_render = True
    apply_display(obj, P)
    set_spline(obj, None, pts_world, radii or [1.0] * len(pts_world))
    return obj


# ----------------------------------------------------------------------
# Spline data
# ----------------------------------------------------------------------
def recalc_handles(sp):
    """Catmull-Rom style handles: smooth curve passing through every point."""
    bps = sp.bezier_points
    n = len(bps)
    if n == 0:
        return
    cyc = sp.use_cyclic_u
    cos = [bp.co.copy() for bp in bps]
    for i, bp in enumerate(bps):
        if cyc:
            prev, nxt = cos[i - 1], cos[(i + 1) % n]
        else:
            prev, nxt = cos[max(i - 1, 0)], cos[min(i + 1, n - 1)]
        tan = (nxt - prev) / 6.0
        if not cyc and (i == 0 or i == n - 1):
            tan = (nxt - prev) / 3.0
        bp.handle_left_type = "FREE"
        bp.handle_right_type = "FREE"
        bp.handle_left = cos[i] - tan
        bp.handle_right = cos[i] + tan


def control_points(obj):
    """[(world points, radii, cyclic)] — the editable control points of every spline."""
    mw = obj.matrix_world
    out = []
    for sp in obj.data.splines:
        if sp.type == "BEZIER":
            out.append(([mw @ bp.co for bp in sp.bezier_points],
                        [bp.radius for bp in sp.bezier_points], sp.use_cyclic_u))
        else:
            out.append(([mw @ Vector(p.co[:3]) for p in sp.points],
                        [p.radius for p in sp.points], sp.use_cyclic_u))
    return out


def set_spline(obj, index, pts_world, radii, cyclic=False):
    """Replace spline `index` (None = append) with a Bezier spline through the points."""
    inv = obj.matrix_world.inverted()
    splines = obj.data.splines
    if index is not None and index < len(splines):
        old = splines[index]
        cyclic = old.use_cyclic_u
        if old.type == "BEZIER" and len(old.bezier_points) == len(pts_world):
            for bp, p, r in zip(old.bezier_points, pts_world, radii):
                bp.co = inv @ p
                bp.radius = r
            recalc_handles(old)
            obj.data.update_tag()
            return index
        # The point count changed: rebuild the spline (Bezier points cannot be removed).
        others = [_dump(s) for k, s in enumerate(splines) if k != index]
        splines.clear()
        for k, d in enumerate(others):
            if k == index:
                _new_bezier(obj, [inv @ p for p in pts_world], radii, cyclic)
            _load(obj, d)
        if index >= len(others):
            _new_bezier(obj, [inv @ p for p in pts_world], radii, cyclic)
        obj.data.update_tag()
        return index
    _new_bezier(obj, [inv @ p for p in pts_world], radii, cyclic)
    obj.data.update_tag()
    return len(splines) - 1


def _new_bezier(obj, pts_local, radii, cyclic):
    sp = obj.data.splines.new("BEZIER")
    sp.bezier_points.add(len(pts_local) - 1)
    for bp, p, r in zip(sp.bezier_points, pts_local, radii):
        bp.co = p
        bp.radius = r
    sp.use_cyclic_u = cyclic
    recalc_handles(sp)
    return sp


def _dump(sp):
    if sp.type == "BEZIER":
        return ("BEZIER", [(bp.co.copy(), bp.handle_left.copy(), bp.handle_right.copy(), bp.radius)
                           for bp in sp.bezier_points], sp.use_cyclic_u)
    return (sp.type, [(Vector(p.co), p.radius) for p in sp.points], sp.use_cyclic_u)


def _load(obj, d):
    kind, pts, cyc = d
    sp = obj.data.splines.new(kind)
    if kind == "BEZIER":
        sp.bezier_points.add(len(pts) - 1)
        for bp, (co, hl, hr, r) in zip(sp.bezier_points, pts):
            bp.handle_left_type = bp.handle_right_type = "FREE"
            bp.co, bp.handle_left, bp.handle_right, bp.radius = co, hl, hr, r
    else:
        sp.points.add(len(pts) - 1)
        for p, (co, r) in zip(sp.points, pts):
            p.co, p.radius = co, r
    sp.use_cyclic_u = cyc


def evaluated(obj, resolution=10):
    """Smooth world-space polylines for every spline: [(points, radii, cyclic)]."""
    mw = obj.matrix_world
    out = []
    for sp in obj.data.splines:
        cyc = sp.use_cyclic_u
        if sp.type == "BEZIER":
            bps = sp.bezier_points
            n = len(bps)
            if n < 2:
                continue
            pts, rad = [], []
            for i in range(n if cyc else n - 1):
                a, b = bps[i], bps[(i + 1) % n]
                seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, resolution + 1)
                for k in range(resolution):
                    t = k / resolution
                    pts.append(mw @ seg[k])
                    rad.append(a.radius * (1.0 - t) + b.radius * t)
            if not cyc:
                pts.append(mw @ bps[-1].co)
                rad.append(bps[-1].radius)
        else:
            ps = sp.points
            if len(ps) < 2:
                continue
            pts = [mw @ Vector(p.co[:3]) for p in ps]
            rad = [p.radius for p in ps]
        out.append((pts, rad, cyc))
    return out


def resample(pts, rad, step, closed=False):
    """Uniform arc-length resampling."""
    if closed:
        pts = pts + [pts[0]]
        rad = rad + [rad[0]]
    lens = [0.0]
    for i in range(1, len(pts)):
        lens.append(lens[-1] + (pts[i] - pts[i - 1]).length)
    total = lens[-1]
    if total < 1e-9:
        return [], []
    count = max(2, int(math.ceil(total / step)) + 1)
    out_p, out_r = [], []
    j = 0
    for k in range(count):
        s = total * k / (count - 1)
        while j < len(lens) - 2 and lens[j + 1] < s:
            j += 1
        seg = lens[j + 1] - lens[j]
        u = 0.0 if seg < 1e-12 else (s - lens[j]) / seg
        out_p.append(pts[j].lerp(pts[j + 1], u))
        out_r.append(rad[j] * (1.0 - u) + rad[j + 1] * u)
    if closed:
        out_p.pop()
        out_r.pop()
    return out_p, out_r


def decimate(pts, radii, spacing):
    """Keep roughly one control point every `spacing` along a dense path."""
    n = len(pts)
    keep = [0]
    acc = 0.0
    for i in range(1, n):
        acc += (pts[i] - pts[i - 1]).length
        if acc >= spacing:
            keep.append(i)
            acc = 0.0
    if keep[-1] != n - 1:
        if len(keep) > 1 and acc < spacing * 0.4:
            keep[-1] = n - 1
        else:
            keep.append(n - 1)
    return [pts[k] for k in keep], [radii[k] for k in keep]


def unique_name(base):
    i = 1
    while True:
        name = "%s_%03d" % (base, i)
        if name not in bpy.data.objects:
            return name
        i += 1
