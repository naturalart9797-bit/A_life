"""Viewport overlay: guide control points, paint weights, brush circle, stroke preview."""

import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import guides

brush = {"active": False, "hover": False, "x": 0, "y": 0, "r": 60, "mode": "ADD"}
hover = {"x": -1000, "y": -1000}
stroke = {"active": False, "points": []}
edit_state = {"active": False, "obj": "", "si": 0, "pi": 0}

_paint_cache = {"key": None, "batch": None, "version": 0}
_handles = []


def paint_changed():
    _paint_cache["version"] += 1


def _shader(name):
    try:
        return gpu.shader.from_builtin(name)
    except (ValueError, SystemError):
        return gpu.shader.from_builtin("3D_" + name)


def _active_tool(context):
    try:
        return context.workspace.tools.from_space_view3d_mode(context.mode, create=False).idname
    except Exception:  # noqa: BLE001
        return ""


def _posed(target):
    return any(m.type == "ARMATURE" and m.object and m.object.data.pose_position == "POSE"
               for m in target.modifiers)


# ----------------------------------------------------------------------
def _draw_paint(context, P, target):
    vg = target.vertex_groups.get(P.paint_group)
    if vg is None:
        return
    me = target.data
    key = (target.name, vg.name, len(me.vertices), _paint_cache["version"], target.matrix_world.copy().freeze())
    if _paint_cache["key"] != key:
        gi = vg.index
        w = [0.0] * len(me.vertices)
        for v in me.vertices:
            for g in v.groups:
                if g.group == gi:
                    w[v.index] = g.weight
                    break
        mw = target.matrix_world
        nm = mw.to_3x3().inverted().transposed()
        off = max(target.dimensions) * 0.002
        co = [mw @ v.co + (nm @ v.normal).normalized() * off for v in me.vertices]
        if len(me.loop_triangles) == 0:
            me.calc_loop_triangles()
        pos, col = [], []
        for t in me.loop_triangles:
            a, b, c = t.vertices
            if w[a] <= 0.0 and w[b] <= 0.0 and w[c] <= 0.0:
                continue
            for i in (a, b, c):
                pos.append(co[i])
                x = w[i]
                col.append((0.15 + 0.85 * x, 0.9, 0.25 * (1.0 - x), 0.15 + 0.45 * x))
        shader = _shader("SMOOTH_COLOR")
        _paint_cache["batch"] = (shader, batch_for_shader(shader, "TRIS", {"pos": pos, "color": col})) if pos else None
        _paint_cache["key"] = key
    if _paint_cache["batch"] is None:
        return
    shader, batch = _paint_cache["batch"]
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("LESS_EQUAL")
    gpu.state.depth_mask_set(False)
    shader.bind()
    batch.draw(shader)
    gpu.state.depth_mask_set(True)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _draw_points(context, P, target, tool):
    sel = [o for o in guides.guide_objects(target)
           if o.name in context.view_layer.objects and o.visible_get() and o.select_get()]
    editing = tool == "vine_wrap.tool_edit"
    if not sel and not editing:
        return
    objs = sel
    faint = []
    if editing and not sel:
        faint = [o for o in guides.guide_objects(target)
                 if o.name in context.view_layer.objects and o.visible_get()]
    region = context.region
    rv3d = context.region_data
    from bpy_extras import view3d_utils
    mouse = Vector((hover["x"], hover["y"]))

    pts, roots, hot = [], [], None
    best = 14.0
    for o in objs + faint:
        for cps, _r, _c in guides.control_points(o):
            for i, p in enumerate(cps):
                (roots if i == 0 else pts).append(p)
                if editing:
                    co = view3d_utils.location_3d_to_region_2d(region, rv3d, p)
                    if co is not None:
                        d = (co - mouse).length
                        if d < best:
                            best, hot = d, p
    shader = _shader("UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    alpha = 0.95 if objs else 0.5
    for coords, size, color in ((pts, 6.0, (1.0, 1.0, 1.0, alpha)), (roots, 8.0, (0.3, 1.0, 0.4, alpha))):
        if coords:
            gpu.state.point_size_set(size)
            batch = batch_for_shader(shader, "POINTS", {"pos": coords})
            shader.bind()
            shader.uniform_float("color", color)
            batch.draw(shader)
    if hot is not None:
        gpu.state.point_size_set(11.0)
        batch = batch_for_shader(shader, "POINTS", {"pos": [hot]})
        shader.bind()
        shader.uniform_float("color", (1.0, 0.8, 0.1, 1.0))
        batch.draw(shader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_view():
    context = bpy.context
    P = getattr(context.scene, "vine_wrap", None)
    if P is None or P.target is None:
        return
    target = P.target
    tool = _active_tool(context)
    if (P.show_paint and tool == "vine_wrap.tool_paint") or P.show_paint_always:
        _draw_paint(context, P, target)
    if P.show_points and not _posed(target):
        _draw_points(context, P, target, tool)
    if stroke["active"] and len(stroke["points"]) >= 2:
        shader = _shader("POLYLINE_UNIFORM_COLOR")
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": stroke["points"]})
        gpu.state.blend_set("ALPHA")
        shader.bind()
        shader.uniform_float("viewportSize", (context.region.width, context.region.height))
        shader.uniform_float("lineWidth", 3.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 1.0))
        batch.draw(shader)
        gpu.state.blend_set("NONE")


_MODE_COLORS = {"ADD": (0.4, 1.0, 0.4), "ERASE": (1.0, 0.35, 0.35), "SMOOTH": (0.5, 0.8, 1.0)}


def _draw_pixel():
    context = bpy.context
    st = brush
    if not (st["active"] or (st["hover"] and _active_tool(context) == "vine_wrap.tool_paint")):
        return
    r = st["r"]
    cx, cy = st["x"], st["y"]
    pts = [(cx + math.cos(a) * r, cy + math.sin(a) * r) for a in (math.tau * i / 48 for i in range(48))]
    c = _MODE_COLORS.get(st["mode"], (1, 1, 1)) if st["active"] else (1.0, 1.0, 1.0)
    shader = _shader("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_LOOP", {"pos": pts})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (c[0], c[1], c[2], 0.9 if st["active"] else 0.6))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def register():
    if _handles:
        return
    sv = bpy.types.SpaceView3D
    _handles.append(sv.draw_handler_add(_draw_view, (), "WINDOW", "POST_VIEW"))
    _handles.append(sv.draw_handler_add(_draw_pixel, (), "WINDOW", "POST_PIXEL"))


def unregister():
    sv = bpy.types.SpaceView3D
    for h in _handles:
        try:
            sv.draw_handler_remove(h, "WINDOW")
        except ValueError:
            pass
    _handles.clear()
