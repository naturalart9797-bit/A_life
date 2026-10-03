"""Guide groups.

Each guide group is a curve object (POLY splines, body-local coordinates,
parented to the body). The groom tools edit these splines directly in Object
mode and the viewport overlay draws them, so the curve objects themselves stay
hidden.
"""

import math

import bpy
from mathutils import Vector
from mathutils.geometry import interpolate_bezier

KIND_BODY = "BODY"
KIND_SKIRT = "SKIRT"
AUTO_PROP = "vine_dress_auto"

KIND_ITEMS = [
    (KIND_BODY, "体表面", "体の表面に吸着するつる（服の部分）", "MOD_SHRINKWRAP", 0),
    (KIND_SKIRT, "スカート/空間", "体から離れて空間に浮かぶつる（水中スカートの部分）", "MOD_CLOTH", 1),
]

KIND_COLORS = {
    KIND_BODY: (0.25, 1.0, 0.35),
    KIND_SKIRT: (0.25, 0.65, 1.0),
}

_PALETTE = [
    (1.0, 0.55, 0.2), (0.95, 0.3, 0.6), (0.7, 0.45, 1.0), (1.0, 0.9, 0.3),
    (0.3, 1.0, 0.85), (0.6, 1.0, 0.3), (1.0, 0.4, 0.35), (0.45, 0.8, 1.0),
]


def redraw(_self=None, _ctx=None):
    wm = bpy.context.window_manager
    for win in getattr(wm, "windows", []):
        for area in win.screen.areas:
            if area.type in {"VIEW_3D", "NODE_EDITOR"}:
                area.tag_redraw()


def _changed(self, _ctx):
    redraw()
    from . import groom_tree
    groom_tree.schedule_for_body(self.body)


class GuideSettings(bpy.types.PropertyGroup):
    body: bpy.props.PointerProperty(
        name="人物", type=bpy.types.Object,
        description="このガイドグループが属する人物メッシュ")
    kind: bpy.props.EnumProperty(name="種類", items=KIND_ITEMS, default=KIND_BODY, update=_changed)
    color: bpy.props.FloatVectorProperty(name="表示色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                         default=(0.25, 1.0, 0.35), update=redraw)
    visible: bpy.props.BoolProperty(name="表示", default=True, update=redraw,
                                    description="ビューポートにガイドを表示する")
    locked: bpy.props.BoolProperty(name="ロック", default=False, update=redraw,
                                   description="ブラシで編集できないようにする")


def is_guide(obj):
    return obj is not None and obj.type == "CURVE" and obj.vine_guide.body is not None


def is_guide_of(obj, body):
    return obj.type == "CURVE" and body is not None and obj.vine_guide.body == body


def guide_objects(body):
    return [o for o in bpy.data.objects if is_guide_of(o, body)]


def guide_collection(context, body):
    name = body.name + "_VineGuides"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children and not any(
            coll.name in c.children for c in bpy.data.collections):
        context.scene.collection.children.link(coll)
    return coll


def new_group(context, body, kind, name, auto=False, color=None):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    obj = bpy.data.objects.new(name, cu)
    gs = obj.vine_guide
    gs.body = body
    gs.kind = kind
    if color is None:
        if auto:
            color = KIND_COLORS[kind]
        else:
            color = _PALETTE[len(guide_objects(body)) % len(_PALETTE)]
    gs.color = color
    if auto:
        obj[AUTO_PROP] = kind
    guide_collection(context, body).objects.link(obj)
    obj.parent = body
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()
    # matrix_world is only refreshed on depsgraph update; set it now so points
    # written right away land in the right place.
    obj.matrix_world = body.matrix_world.copy()
    obj.hide_render = True
    obj.display_type = "WIRE"
    try:
        obj.hide_set(True)  # the overlay draws the guides
    except RuntimeError:
        pass
    return obj


def auto_group(context, body, kind, name):
    """The auto-generated group of a kind, reused so node links stay valid."""
    for o in guide_objects(body):
        if o.get(AUTO_PROP) == kind:
            o.data.splines.clear()
            return o
    return new_group(context, body, kind, name, auto=True)


def remove_group(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data.users == 0:
        bpy.data.curves.remove(data)


# ----------------------------------------------------------------------
# Spline IO
# ----------------------------------------------------------------------
def write_paths(obj, paths, ctrl_spacing):
    """Write vine paths (world space) into a group as POLY splines."""
    inv = obj.matrix_world.inverted()
    for path in paths:
        pts = path.points
        n = len(pts)
        if n < 2:
            continue
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
        add_spline(obj, [inv @ pts[k] for k in keep], [path.radii[k] for k in keep],
                   closed=path.closed, local=True)


def add_spline(obj, pts, radii, closed=False, local=False, select=False):
    inv = None if local else obj.matrix_world.inverted()
    sp = obj.data.splines.new("POLY")
    sp.points.add(len(pts) - 1)
    co = []
    for p in pts:
        q = p if local else inv @ p
        co += (q.x, q.y, q.z, 1.0)
    sp.points.foreach_set("co", co)
    sp.points.foreach_set("radius", list(radii))
    sp.points.foreach_set("select", [select] * len(pts))
    sp.use_cyclic_u = closed
    return sp


def read_splines(obj, resolution=12):
    """Return [(points_world, radii, closed)] for every spline of a group."""
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
