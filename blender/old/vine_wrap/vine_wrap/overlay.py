"""Viewport overlay: guide control points and the curve being placed."""

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import guides

hover = {"x": -1000, "y": -1000}
place = {"active": False, "points": [], "cursor": None}
edit_state = {"active": False, "obj": "", "si": 0, "pi": 0}

_handles = []


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
def _draw_points(context, P, target, tool):
    sel = [o for o in guides.guide_objects(target)
           if o.name in context.view_layer.objects and o.visible_get() and o.select_get()]
    editing = tool in {"vine_wrap.tool_edit", "vine_wrap.tool_curve"}
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


def _catmull_rom(pts, seg=10):
    n = len(pts)
    if n < 3:
        return list(pts)
    out = []
    for i in range(n - 1):
        p0 = pts[max(i - 1, 0)]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[min(i + 2, n - 1)]
        for k in range(seg):
            t = k / seg
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2.0 * p1) + (-p0 + p2) * t + (2.0 * p0 - 5.0 * p1 + 4.0 * p2 - p3) * t2
                              + (-p0 + 3.0 * p1 - 3.0 * p2 + p3) * t3))
    out.append(pts[-1])
    return out


def _draw_place(context):
    pts = list(place["points"])
    cur = place["cursor"]
    shader = _shader("POLYLINE_UNIFORM_COLOR")
    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE")
    vp = (context.region.width, context.region.height)
    if len(pts) >= 2:
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": _catmull_rom(pts)})
        shader.bind()
        shader.uniform_float("viewportSize", vp)
        shader.uniform_float("lineWidth", 3.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 1.0))
        batch.draw(shader)
    if pts and cur is not None:
        tail = _catmull_rom(pts[-2:] + [cur]) if len(pts) >= 2 else [pts[-1], cur]
        if len(pts) >= 2:
            tail = tail[len(tail) // 2:]
        batch = batch_for_shader(shader, "LINE_STRIP", {"pos": tail})
        shader.bind()
        shader.uniform_float("viewportSize", vp)
        shader.uniform_float("lineWidth", 2.0)
        shader.uniform_float("color", (1.0, 0.85, 0.2, 0.5))
        batch.draw(shader)
    ushader = _shader("UNIFORM_COLOR")
    for coords, size, color in ((pts[1:], 8.0, (1.0, 1.0, 1.0, 1.0)), (pts[:1], 10.0, (0.3, 1.0, 0.4, 1.0)),
                                ([cur] if cur is not None else [], 7.0, (1.0, 0.85, 0.2, 0.8))):
        if coords:
            gpu.state.point_size_set(size)
            b = batch_for_shader(ushader, "POINTS", {"pos": coords})
            ushader.bind()
            ushader.uniform_float("color", color)
            b.draw(ushader)
    gpu.state.point_size_set(1.0)
    gpu.state.blend_set("NONE")


def _draw_view():
    context = bpy.context
    P = getattr(context.scene, "vine_wrap", None)
    if P is None or P.target is None:
        return
    target = P.target
    tool = _active_tool(context)
    if P.show_points and not _posed(target):
        _draw_points(context, P, target, tool)
    if place["active"]:
        _draw_place(context)


def register():
    if _handles:
        return
    sv = bpy.types.SpaceView3D
    _handles.append(sv.draw_handler_add(_draw_view, (), "WINDOW", "POST_VIEW"))


def unregister():
    sv = bpy.types.SpaceView3D
    for h in _handles:
        try:
            sv.draw_handler_remove(h, "WINDOW")
        except ValueError:
            pass
    _handles.clear()
