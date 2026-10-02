"""Guide curves: editable curve objects that the vines are built from."""

import math

import bpy
from mathutils import Vector
from mathutils.geometry import interpolate_bezier

KIND_BODY = "BODY"
KIND_SKIRT = "SKIRT"
AUTO_PROP = "vine_dress_auto"

KIND_COLORS = {
    KIND_BODY: (0.2, 1.0, 0.3, 1.0),
    KIND_SKIRT: (0.2, 0.6, 1.0, 1.0),
}


class GuideSettings(bpy.types.PropertyGroup):
    body: bpy.props.PointerProperty(
        name="人物", type=bpy.types.Object,
        description="このガイドが属する人物メッシュ")
    kind: bpy.props.EnumProperty(
        name="種類",
        items=[
            (KIND_BODY, "体表面", "体の表面に吸着するつる（服の部分）"),
            (KIND_SKIRT, "スカート/空間", "体から離れて空間に浮かぶつる（水中スカートの部分）"),
        ],
        default=KIND_BODY,
        update=lambda self, ctx: _update_color(self.id_data))
    radius: bpy.props.FloatProperty(name="太さ倍率", default=1.0, min=0.0, soft_max=5.0)
    leaves: bpy.props.FloatProperty(name="葉の量倍率", default=1.0, min=0.0, soft_max=5.0)
    strands: bpy.props.IntProperty(name="本数(0=共通)", default=0, min=0, max=12,
                                   description="1本のガイドに沿わせるつるの本数。0なら共通設定を使う")
    enabled: bpy.props.BoolProperty(name="使用", default=True)


def _update_color(obj):
    if obj is not None and hasattr(obj, "vine_guide"):
        obj.color = KIND_COLORS.get(obj.vine_guide.kind, (1, 1, 1, 1))


def is_guide_of(obj, body):
    return obj.type == "CURVE" and obj.vine_guide.body == body


def guide_objects(body, only_enabled=False):
    out = [o for o in bpy.data.objects if is_guide_of(o, body)]
    if only_enabled:
        out = [o for o in out if o.vine_guide.enabled]
    return out


def guide_collection(context, body):
    name = body.name + "_VineGuides"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children and not any(
            coll.name in c.children for c in bpy.data.collections):
        context.scene.collection.children.link(coll)
    return coll


def new_guide_object(context, body, kind, name, auto=False):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.resolution_u = 6
    obj = bpy.data.objects.new(name, cu)
    obj.vine_guide.body = body
    obj.vine_guide.kind = kind
    if auto:
        obj[AUTO_PROP] = True
    guide_collection(context, body).objects.link(obj)
    obj.parent = body
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()
    # matrix_world is only refreshed on depsgraph update; set it now so points
    # written right away land in the right place.
    obj.matrix_world = body.matrix_world.copy()
    obj.show_in_front = True
    _update_color(obj)
    return obj


def remove_guides(body, auto_only):
    for obj in guide_objects(body):
        if auto_only and not obj.get(AUTO_PROP):
            continue
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if data.users == 0:
            bpy.data.curves.remove(data)


# ----------------------------------------------------------------------
def write_paths(obj, paths, ctrl_spacing):
    """Write vine paths (world space) into a curve object as Bezier splines."""
    inv = obj.matrix_world.inverted()
    for path in paths:
        pts = path.points
        n = len(pts)
        if n < 2:
            continue
        # Keep roughly one control point per ctrl_spacing of length.
        keep = [0]
        acc = 0.0
        for i in range(1, n):
            acc += (pts[i] - pts[i - 1]).length
            if acc >= ctrl_spacing:
                keep.append(i)
                acc = 0.0
        if path.closed:
            if len(keep) > 3 and acc < ctrl_spacing * 0.5:
                keep.pop()
        elif keep[-1] != n - 1:
            if len(keep) > 1 and acc < ctrl_spacing * 0.4:
                keep[-1] = n - 1
            else:
                keep.append(n - 1)
        if len(keep) < 2:
            continue

        sp = obj.data.splines.new("BEZIER")
        sp.bezier_points.add(len(keep) - 1)
        sp.use_cyclic_u = path.closed
        m = len(keep)
        local = [inv @ pts[k] for k in keep]
        for j, k in enumerate(keep):
            bp = sp.bezier_points[j]
            if path.closed:
                prev, nxt = local[j - 1], local[(j + 1) % m]
            else:
                prev, nxt = local[max(j - 1, 0)], local[min(j + 1, m - 1)]
            tan = (nxt - prev) / 6.0
            bp.co = local[j]
            bp.handle_left = local[j] - tan
            bp.handle_right = local[j] + tan
            bp.handle_left_type = "AUTO"
            bp.handle_right_type = "AUTO"
            bp.radius = path.radii[k]


def read_splines(obj, resolution=12):
    """Return [(points_world, radii, closed)] for every spline of a curve object."""
    mw = obj.matrix_world
    out = []
    for sp in obj.data.splines:
        closed = sp.use_cyclic_u
        if sp.type == "BEZIER":
            bps = sp.bezier_points
            n = len(bps)
            if n < 2:
                continue
            pts, rad = [], []
            segs = n if closed else n - 1
            for i in range(segs):
                a, b = bps[i], bps[(i + 1) % n]
                seg = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, resolution + 1)
                for k in range(resolution):
                    pts.append(mw @ seg[k])
                    t = k / resolution
                    rad.append(a.radius * (1.0 - t) + b.radius * t)
            if not closed:
                pts.append(mw @ bps[-1].co)
                rad.append(bps[-1].radius)
        else:
            ps = sp.points
            if len(ps) < 2:
                continue
            pts = [mw @ Vector(p.co[:3]) for p in ps]
            rad = [p.radius for p in ps]
        out.append((pts, rad, closed))
    return out


def resample(pts, rad, step, closed):
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


def snap_object(obj, sampler, offset=0.0):
    """Move every control point of a guide onto the body surface (+ a small hover offset)."""
    mw = obj.matrix_world
    inv = mw.inverted()
    moved = 0
    for sp in obj.data.splines:
        if sp.type == "BEZIER":
            for bp in sp.bezier_points:
                hit = sampler.nearest(mw @ bp.co)
                if hit is None:
                    continue
                new = inv @ (hit.loc + hit.normal * offset)
                d = new - bp.co
                bp.co = new
                bp.handle_left = bp.handle_left + d
                bp.handle_right = bp.handle_right + d
                moved += 1
        else:
            for p in sp.points:
                hit = sampler.nearest(mw @ Vector(p.co[:3]))
                if hit is None:
                    continue
                new = inv @ (hit.loc + hit.normal * offset)
                p.co = (new.x, new.y, new.z, p.co[3])
                moved += 1
    obj.data.update_tag()
    return moved
