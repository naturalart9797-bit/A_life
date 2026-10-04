"""Viewport overlay: guide groups in their colours, brush circle, stroke preview."""

import math

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from . import guides

brush_state = {"active": False, "hover": False, "x": 0, "y": 0, "r": 50, "mode": "COMB", "invert": False}
stroke_state = {"active": False, "points": [], "color": (1, 1, 1)}

_handles = []


def _shader(name):
    try:
        return gpu.shader.from_builtin(name)
    except (ValueError, SystemError):
        return gpu.shader.from_builtin("3D_" + name)


def _armature_posed(body):
    for m in body.modifiers:
        if m.type == "ARMATURE" and m.object and m.object.data.pose_position == "POSE":
            return True
    return False


def _draw_guides():
    context = bpy.context
    scene = context.scene
    P = getattr(scene, "vine_dress", None)
    if P is None or not P.show_overlay or P.body is None:
        return
    body = P.body
    if not P.overlay_in_pose and _armature_posed(body):
        return
    region = context.region
    coords, cols = [], []
    sel_coords = []
    for obj in guides.guide_objects(body):
        gs = obj.vine_guide
        if not gs.visible:
            continue
        mw = obj.matrix_world
        base = tuple(gs.color)
        dim = 0.45 if gs.locked else 1.0
        for sp in obj.data.splines:
            if sp.type == "BEZIER":
                pts = [mw @ bp.co for bp in sp.bezier_points]
                sel = any(bp.select_control_point for bp in sp.bezier_points)
            else:
                pts = [mw @ Vector(p.co[:3]) for p in sp.points]
                sel = any(p.select for p in sp.points)
            if len(pts) < 2:
                continue
            if sel:
                c = (min(1.0, base[0] * 0.4 + 0.6), min(1.0, base[1] * 0.4 + 0.6),
                     min(1.0, base[2] * 0.4 + 0.6), 1.0)
            else:
                c = (base[0] * dim, base[1] * dim, base[2] * dim, 0.9)
            segs = list(zip(pts[:-1], pts[1:]))
            if sp.use_cyclic_u:
                segs.append((pts[-1], pts[0]))
            n = len(pts)
            for k, (a, b) in enumerate(segs):
                # Fade a little toward the tip so the growth direction is readable.
                fa = 1.0 - 0.5 * k / n
                fb = 1.0 - 0.5 * (k + 1) / n
                coords += (a, b)
                cols += ((c[0] * fa, c[1] * fa, c[2] * fa, c[3]), (c[0] * fb, c[1] * fb, c[2] * fb, c[3]))
            if sel:
                sel_coords += pts
    if not coords:
        return

    gpu.state.blend_set("ALPHA")
    gpu.state.depth_test_set("NONE" if P.overlay_xray else "LESS_EQUAL")
    shader = _shader("POLYLINE_FLAT_COLOR")
    batch = batch_for_shader(shader, "LINES", {"pos": coords, "color": cols})
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", P.overlay_line_width)
    batch.draw(shader)

    if sel_coords:
        pshader = _shader("UNIFORM_COLOR")
        gpu.state.point_size_set(4.0)
        pb = batch_for_shader(pshader, "POINTS", {"pos": sel_coords})
        pshader.bind()
        pshader.uniform_float("color", (1.0, 1.0, 1.0, 0.8))
        pb.draw(pshader)
        gpu.state.point_size_set(1.0)
    gpu.state.depth_test_set("NONE")
    gpu.state.blend_set("NONE")


def _draw_stroke():
    if not stroke_state["active"] or len(stroke_state["points"]) < 2:
        return
    region = bpy.context.region
    pts = stroke_state["points"]
    c = stroke_state["color"]
    shader = _shader("POLYLINE_UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_STRIP", {"pos": pts})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("viewportSize", (region.width, region.height))
    shader.uniform_float("lineWidth", 3.0)
    shader.uniform_float("color", (c[0], c[1], c[2], 1.0))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


_MODE_COLORS = {
    "COMB": (1.0, 0.8, 0.3), "SMOOTH": (0.5, 0.9, 1.0), "GROW": (0.5, 1.0, 0.5),
    "THICK": (1.0, 0.5, 0.9), "ERASE": (1.0, 0.3, 0.3), "SELECT": (1.0, 1.0, 1.0),
}


def _draw_brush():
    st = brush_state
    if not (st["active"] or st["hover"]):
        return
    tool = None
    try:
        tool = bpy.context.workspace.tools.from_space_view3d_mode(bpy.context.mode, create=False)
    except Exception:  # noqa: BLE001
        pass
    if not st["active"] and (tool is None or not tool.idname.startswith("vine_dress.")):
        return
    r = st["r"]
    cx, cy = st["x"], st["y"]
    pts = [(cx + math.cos(a) * r, cy + math.sin(a) * r)
           for a in (math.tau * i / 48 for i in range(48))]
    c = _MODE_COLORS.get(st["mode"], (1, 1, 1))
    if st.get("invert"):
        c = (c[0] * 0.6, c[1] * 0.6, c[2] * 0.6)
    shader = _shader("UNIFORM_COLOR")
    batch = batch_for_shader(shader, "LINE_LOOP", {"pos": pts})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (c[0], c[1], c[2], 0.9 if st["active"] else 0.5))
    batch.draw(shader)
    gpu.state.blend_set("NONE")


def register():
    if _handles:
        return
    sv = bpy.types.SpaceView3D
    _handles.append(sv.draw_handler_add(_draw_guides, (), "WINDOW", "POST_VIEW"))
    _handles.append(sv.draw_handler_add(_draw_stroke, (), "WINDOW", "POST_VIEW"))
    _handles.append(sv.draw_handler_add(_draw_brush, (), "WINDOW", "POST_PIXEL"))


def unregister():
    sv = bpy.types.SpaceView3D
    for h in _handles:
        try:
            sv.draw_handler_remove(h, "WINDOW")
        except ValueError:
            pass
    _handles.clear()
