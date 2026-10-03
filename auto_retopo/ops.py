# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators: place guides on the scan, generate the retopology."""

import blf
import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import face_template
from . import fit
from . import guides as G
from . import hand_builder

NAV_TYPES = {
    'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE',
    'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE',
    'MOUSESMARTZOOM', 'NDOF_MOTION', 'HOME', 'ACCENT_GRAVE',
    'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5',
    'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9', 'NUMPAD_PERIOD',
    'NUMPAD_SLASH', 'NUMPAD_ASTERIX', 'NUMPAD_MINUS', 'NUMPAD_PLUS',
}
PICK_PX = 12


# ---------------------------------------------------------------------------
# Scan surface (object local space)
# ---------------------------------------------------------------------------

class ScanSurface:

    def __init__(self, context, obj):
        dg = context.evaluated_depsgraph_get()
        ob_eval = obj.evaluated_get(dg)
        me = ob_eval.to_mesh()
        try:
            me.calc_loop_triangles()
            nv, nt = len(me.vertices), len(me.loop_triangles)
            co = np.empty(nv * 3, np.float32)
            me.vertices.foreach_get("co", co)
            tris = np.empty(nt * 3, np.int32)
            me.loop_triangles.foreach_get("vertices", tris)
        finally:
            ob_eval.to_mesh_clear()
        co = co.reshape(-1, 3).astype(float)
        self.co = co
        self.size = float(np.linalg.norm(co.max(0) - co.min(0))) if nv else 1.0
        self.bvh = BVHTree.FromPolygons(co.tolist(), tris.reshape(-1, 3).tolist(),
                                        all_triangles=True)

    def ray(self, origin, direction, dist=1e10):
        loc = self.bvh.ray_cast(Vector(origin), Vector(direction), dist)[0]
        return None if loc is None else np.array(loc)

    def nearest(self, p):
        loc = self.bvh.find_nearest(Vector(p))[0]
        return np.array(p, float) if loc is None else np.array(loc)

    def normal(self, p):
        nor = self.bvh.find_nearest(Vector(p))[1]
        return None if nor is None else np.array(nor)

    def nearest_many(self, V):
        out = np.empty_like(V)
        for i, p in enumerate(V):
            loc = self.bvh.find_nearest(Vector(p))[0]
            out[i] = p if loc is None else loc
        return out


def _settings(context):
    return context.scene.auto_retopo


def _defs(context):
    s = _settings(context)
    return G.definitions(s.kind, s.symmetric)


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _points(coords, color, size):
    if not coords:
        return
    try:
        sh = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
    except (ValueError, SystemError):
        sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.point_size_set(size)
    batch = batch_for_shader(sh, 'POINTS', {"pos": coords})
    sh.uniform_float("color", color)
    batch.draw(sh)
    gpu.state.point_size_set(1.0)


def _text(x, y, s, size=13, color=(1, 1, 1, 0.95)):
    blf.size(0, size)
    blf.color(0, *color)
    blf.enable(0, blf.SHADOW)
    blf.shadow(0, 3, 0, 0, 0, 0.85)
    blf.position(0, x, y, 0)
    blf.draw(0, s)
    blf.disable(0, blf.SHADOW)


def _mirrored(context, guides):
    """Preview of the mirrored side in symmetric face mode (local space)."""
    s = _settings(context)
    if s.kind != 'FACE' or not s.symmetric:
        return {}
    centre = [np.array(guides[k]) for k in face_template.CENTER_IDS if k in guides]
    if len(centre) < 3:
        return {}
    plane = fit.mirror_plane(centre)
    out = {}
    for lid, _p, paired, *_r in face_template.LANDMARKS:
        if paired and lid in guides:
            out[lid] = fit.reflect(np.array(guides[lid])[None], plane)[0]
    return out


# ---------------------------------------------------------------------------
# "Where to click" diagram shown while placing guides
# ---------------------------------------------------------------------------

_HAND_SKETCH = {   # back of a right hand, wrist at the bottom (x right, y up)
    "wrist": (0.0, 0.0),
    "thumb": [(-0.42, 0.28), (-0.66, 0.6), (-0.8, 0.9), (-0.88, 1.1)],
    "index": [(-0.32, 1.0), (-0.34, 1.45), (-0.35, 1.72), (-0.355, 1.92)],
    "middle": [(-0.1, 1.05), (-0.1, 1.55), (-0.1, 1.85), (-0.1, 2.07)],
    "ring": [(0.12, 1.0), (0.14, 1.48), (0.15, 1.76), (0.16, 1.96)],
    "pinky": [(0.32, 0.92), (0.36, 1.27), (0.38, 1.48), (0.39, 1.64)],
}
_diagram_cache = {}


def _diagram(kind, symmetric):
    """(segments [(p, q)], {guide id: point}) in a unit box."""
    key = (kind, symmetric)
    if key in _diagram_cache:
        return _diagram_cache[key]
    if kind == 'FACE':
        T = face_template.FaceTemplate(1)
        uv = T.uv
        edges = set()
        for f in T.faces:
            for k in range(4):
                a, b = f[k], f[(k + 1) % 4]
                edges.add((min(a, b), max(a, b)))
        segs = [(uv[a], uv[b]) for a, b in edges]
        pts = {lid: uv[vi] for lid, vi, *_r in T.landmarks}
    else:
        H = _HAND_SKETCH
        segs = []
        pts = {"wrist": np.array(H["wrist"])}
        for f in hand_builder.FINGERS:
            chain = [H["wrist"]] + H[f]
            for a, b in zip(chain, chain[1:]):
                segs.append((np.array(a), np.array(b)))
            for j, p in enumerate(H[f]):
                pts[f"{f}_{j}"] = np.array(p)
    allp = np.array([p for s_ in segs for p in s_])
    lo, hi = allp.min(0), allp.max(0)
    scale = 1.0 / max(hi - lo)
    norm = lambda p: (np.asarray(p) - lo) * scale
    out = ([(norm(a), norm(b)) for a, b in segs], {k: norm(v) for k, v in pts.items()},
           (hi - lo) * scale)
    _diagram_cache[key] = out
    return out


def draw_diagram(context, kind, symmetric, current, placed):
    """Small map in the lower-right corner: the template (face) or a hand
    sketch, with the guide to click next in red and placed guides in green."""
    region = context.region
    ui = context.preferences.system.ui_scale
    size = 230 * ui
    segs, pts, extent = _diagram(kind, symmetric)
    w, h = size * extent[0], size * extent[1]
    x0 = region.width - w - 30 * ui
    y0 = 40 * ui
    to_px = lambda p: (x0 + p[0] * size, y0 + p[1] * size, 0.0)
    gpu.state.blend_set('ALPHA')
    sh = gpu.shader.from_builtin('UNIFORM_COLOR')
    pad = 12 * ui
    bg = [(x0 - pad, y0 - pad, 0), (x0 + w + pad, y0 - pad, 0), (x0 + w + pad, y0 + h + pad, 0),
          (x0 - pad, y0 - pad, 0), (x0 + w + pad, y0 + h + pad, 0), (x0 - pad, y0 + h + pad, 0)]
    batch = batch_for_shader(sh, 'TRIS', {"pos": bg})
    sh.uniform_float("color", (0.05, 0.05, 0.07, 0.75))
    batch.draw(sh)
    lines = [to_px(p) for s_ in segs for p in s_]
    batch = batch_for_shader(sh, 'LINES', {"pos": lines})
    sh.uniform_float("color", (0.7, 0.75, 0.85, 0.5 if kind == 'FACE' else 0.9))
    batch.draw(sh)
    done = [to_px(p) for k, p in pts.items() if k in placed and k != current]
    _points(done, (0.2, 1.0, 0.45, 1.0), 6.0 * ui)
    if current in pts:
        _points([to_px(pts[current])], (1.0, 0.2, 0.2, 1.0), 13.0 * ui)
    gpu.state.blend_set('NONE')


def draw_guides_3d(context, obj, guides, current=None, hover=None):
    mw = obj.matrix_world
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('NONE')
    pts = [mw @ Vector(v) for k, v in guides.items() if k not in (current, hover)]
    _points(pts, (0.2, 1.0, 0.45, 1.0), 10.0)
    mir = _mirrored(context, guides)
    _points([mw @ Vector(v) for v in mir.values()], (0.6, 0.6, 0.6, 0.8), 8.0)
    if current in guides:
        _points([mw @ Vector(guides[current])], (1.0, 0.85, 0.1, 1.0), 14.0)
    if hover in guides:
        _points([mw @ Vector(guides[hover])], (0.3, 0.8, 1.0, 1.0), 14.0)
    gpu.state.blend_set('NONE')


def draw_labels_2d(context, obj, guides, defs):
    region, rv3d = context.region, context.region_data
    order = {d[0]: i + 1 for i, d in enumerate(defs)}
    placed = []
    for k in sorted(guides, key=lambda k: order.get(k, 0)):
        if k not in order:
            continue
        p = view3d_utils.location_3d_to_region_2d(region, rv3d,
                                                  obj.matrix_world @ Vector(guides[k]))
        if p is None:
            continue
        ui = context.preferences.system.ui_scale
        x, y = p.x + 8 * ui, p.y + 6 * ui
        # Nudge labels of guides that sit close together so they never overlap.
        while any(abs(x - qx) < 18 * ui and abs(y - qy) < 14 * ui for qx, qy in placed):
            y += 14 * ui
        placed.append((x, y))
        _text(x, y, str(order[k]), size=12 * ui)


_persistent_handles = []


def _draw_persistent_3d():
    context = bpy.context
    s = getattr(context.scene, "auto_retopo", None)
    obj = context.active_object
    if s is None or not s.show_guides or obj is None or obj.type != 'MESH' \
            or _GuideTool.running:
        return
    g = G.load(obj, s.kind)
    if g:
        draw_guides_3d(context, obj, g)


def _draw_persistent_2d():
    context = bpy.context
    s = getattr(context.scene, "auto_retopo", None)
    obj = context.active_object
    if s is None or not s.show_guides or obj is None or obj.type != 'MESH' \
            or _GuideTool.running:
        return
    g = G.load(obj, s.kind)
    if g:
        draw_labels_2d(context, obj, g, _defs(context))


def register_draw():
    unregister_draw()   # never stack handlers (e.g. after a reinstall)
    _persistent_handles.append(bpy.types.SpaceView3D.draw_handler_add(
        _draw_persistent_3d, (), 'WINDOW', 'POST_VIEW'))
    _persistent_handles.append(bpy.types.SpaceView3D.draw_handler_add(
        _draw_persistent_2d, (), 'WINDOW', 'POST_PIXEL'))


def unregister_draw():
    for h in _persistent_handles:
        bpy.types.SpaceView3D.draw_handler_remove(h, 'WINDOW')
    _persistent_handles.clear()


# ---------------------------------------------------------------------------
# Guide placement tool
# ---------------------------------------------------------------------------

class _GuideTool:
    running = False


class OBJECT_OT_auto_retopo_guides(bpy.types.Operator):
    """Click the guide points on the scan one after another (Enter to finish)"""
    bl_idname = "object.auto_retopo_guides"
    bl_label = "Place Guides"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        # Only one placement session at a time (a second one would draw its
        # own prompt on top of the first).
        return (not _GuideTool.running and ob is not None and ob.type == 'MESH'
                and context.mode == 'OBJECT'
                and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        if _GuideTool.running:
            return {'CANCELLED'}
        self.obj = context.active_object
        self.area = context.area
        self.region = context.region
        self.rv3d = context.region_data
        if self.region is None or self.region.type != 'WINDOW':
            for r in self.area.regions:
                if r.type == 'WINDOW':
                    self.region = r
            self.rv3d = self.area.spaces.active.region_3d
        self.kind = _settings(context).kind
        self.defs = _defs(context)
        self.surface = ScanSurface(context, self.obj)
        self.mw = self.obj.matrix_world.copy()
        self.mw_inv = self.mw.inverted_safe()
        self.initial = G.load(self.obj, self.kind)
        self.guides = dict(self.initial)
        self.current = self._first_missing()
        self.hover = None
        self.drag = None
        self.mouse = Vector((0, 0))
        args = (context,)
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(self._draw3d, args, 'WINDOW', 'POST_VIEW')
        self._h2d = bpy.types.SpaceView3D.draw_handler_add(self._draw2d, args, 'WINDOW', 'POST_PIXEL')
        _GuideTool.running = True
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Guides | LMB: place / drag to move   ←/→: choose guide   Backspace: remove   "
            "Enter: finish   Esc: cancel")
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    # -- helpers -----------------------------------------------------------

    def _first_missing(self):
        for i, d in enumerate(self.defs):
            if d[0] not in self.guides:
                return i
        return len(self.defs)

    def _hit(self, co2d):
        o = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, co2d)
        d = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, co2d)
        lo = self.mw_inv @ o
        ld = (self.mw_inv.to_3x3() @ d).normalized()
        return self.surface.ray(lo, ld)

    def _pick(self, co2d):
        best, bd = None, PICK_PX
        for k, v in self.guides.items():
            p = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, self.mw @ Vector(v))
            if p is not None and (p - co2d).length < bd:
                best, bd = k, (p - co2d).length
        return best

    def _save(self):
        G.save(self.obj, self.kind, self.guides)

    def _finish(self, context):
        bpy.types.SpaceView3D.draw_handler_remove(self._h3d, 'WINDOW')
        bpy.types.SpaceView3D.draw_handler_remove(self._h2d, 'WINDOW')
        _GuideTool.running = False
        context.workspace.status_text_set(None)
        self.area.tag_redraw()

    def _in_region(self, event):
        r = self.region
        x, y = event.mouse_x, event.mouse_y
        if not (r.x <= x < r.x + r.width and r.y <= y < r.y + r.height):
            return False
        for other in self.area.regions:
            if other.type in {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER'} and other.width > 1:
                if other.x <= x < other.x + other.width and other.y <= y < other.y + other.height:
                    return False
        return True

    # -- events ------------------------------------------------------------

    def modal(self, context, event):
        self.area.tag_redraw()
        self.mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        et, ev = event.type, event.value

        if self.drag is not None:
            if et == 'MOUSEMOVE':
                hit = self._hit(self.mouse)
                if hit is not None:
                    self.guides[self.drag] = tuple(hit)
            elif et == 'LEFTMOUSE' and ev == 'RELEASE':
                self.drag = None
                self._save()
            return {'RUNNING_MODAL'}

        if et in {'RET', 'NUMPAD_ENTER'} and ev == 'PRESS':
            self._save()
            self._finish(context)
            missing = len(self.defs) - sum(d[0] in self.guides for d in self.defs)
            if missing:
                self.report({'WARNING'}, f"{missing} guide(s) still missing")
            return {'FINISHED'}
        if et == 'ESC' and ev == 'PRESS':
            self.guides = dict(self.initial)
            self._save()
            self._finish(context)
            return {'CANCELLED'}
        if et in {'BACK_SPACE', 'DEL'} and ev == 'PRESS' or \
                (et == 'Z' and ev == 'PRESS' and (event.ctrl or event.oskey)):
            # Remove the guide before the current one (or the current one).
            idx = self.current if self.current < len(self.defs) and \
                self.defs[self.current][0] in self.guides else self.current - 1
            while idx >= 0 and self.defs[idx][0] not in self.guides:
                idx -= 1
            if idx >= 0:
                del self.guides[self.defs[idx][0]]
                self.current = idx
                self._save()
            return {'RUNNING_MODAL'}
        if et in {'RIGHT_ARROW', 'DOWN_ARROW'} and ev == 'PRESS':
            self.current = min(self.current + 1, len(self.defs) - 1)
            return {'RUNNING_MODAL'}
        if et in {'LEFT_ARROW', 'UP_ARROW'} and ev == 'PRESS':
            self.current = max(self.current - 1, 0)
            return {'RUNNING_MODAL'}

        if not self._in_region(event):
            return {'PASS_THROUGH'}
        if et in NAV_TYPES or et.startswith('NDOF') or \
                (event.alt and et in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}):
            return {'PASS_THROUGH'}
        if et == 'MOUSEMOVE':
            self.hover = self._pick(self.mouse)
            return {'RUNNING_MODAL'}
        if et == 'LEFTMOUSE' and ev == 'PRESS':
            picked = self._pick(self.mouse)
            if picked is not None:
                self.drag = picked
                return {'RUNNING_MODAL'}
            if self.current >= len(self.defs):
                return {'RUNNING_MODAL'}
            hit = self._hit(self.mouse)
            if hit is not None:
                self.guides[self.defs[self.current][0]] = tuple(hit)
                self._save()
                self.current = self._first_missing()
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    # -- drawing -----------------------------------------------------------

    def _draw3d(self, context):
        if context.area != self.area:
            return
        cur = self.defs[self.current][0] if self.current < len(self.defs) else None
        draw_guides_3d(context, self.obj, self.guides, current=cur, hover=self.hover)

    def _draw2d(self, context):
        if context.area != self.area:
            return
        draw_labels_2d(context, self.obj, self.guides, self.defs)
        n = len(self.defs)
        done = sum(d[0] in self.guides for d in self.defs)
        lines = []   # (text, size, colour), top to bottom
        if self.current < n:
            lid, ja, en = self.defs[self.current]
            state = "（配置済み・クリックで置き直し）" if lid in self.guides else ""
            lines.append((f"ガイド {self.current + 1}/{n}：{ja}{state}", 18, (1.0, 0.9, 0.3, 1.0)))
            lines.append((en, 13, (1, 1, 1, 0.95)))
        else:
            lines.append((f"全 {n} 点 配置済み — Enter で確定", 18, (0.4, 1.0, 0.5, 1.0)))
        lines.append((f"{done}/{n} 配置済み   LMB: 配置 / ドラッグで移動   ←→: ガイド選択   "
                      "Backspace: 1つ戻す   Enter: 確定   Esc: 取消", 12, (1, 1, 1, 0.95)))
        _text_block(context, 24, 60, lines)
        cur = self.defs[self.current][0] if self.current < n else None
        draw_diagram(context, self.kind, _settings(context).symmetric, cur, set(self.guides))


def _text_block(context, x, y, lines):
    """Stack lines upwards from (x, y) using the real text height, so lines
    never overlap whatever the UI scale is."""
    ui = context.preferences.system.ui_scale
    for text, size, colour in reversed(lines):
        blf.size(0, size * ui)
        h = blf.dimensions(0, "Hgあ")[1]
        _text(x * ui, y, text, size=size * ui, color=colour)
        y += h + 8 * ui


# ---------------------------------------------------------------------------
# Generate / clear
# ---------------------------------------------------------------------------

class OBJECT_OT_auto_retopo_generate(bpy.types.Operator):
    """Build the retopology mesh from the guides"""
    bl_idname = "object.auto_retopo_generate"
    bl_label = "Generate Retopology"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.active_object
        return ob is not None and ob.type == 'MESH' and context.mode == 'OBJECT'

    def execute(self, context):
        obj = context.active_object
        s = _settings(context)
        defs = _defs(context)
        g = G.load(obj, s.kind)
        missing = [d for d in defs if d[0] not in g]
        if missing:
            self.report({'ERROR'}, "ガイドが足りません: " + "、".join(d[1] for d in missing[:6])
                        + (" …" if len(missing) > 6 else ""))
            return {'CANCELLED'}
        surf = ScanSurface(context, obj)
        pts = {k: np.array(v, float) for k, v in g.items()}
        try:
            if s.kind == 'FACE':
                V, F = face_template.build_face(pts, surf.nearest_many, density=s.face_density,
                                                symmetric=s.symmetric, iters=s.iterations,
                                                samples=surf.co, ray=surf.ray)
                F = fit.orient_faces(V, F, surf.normal)
            else:
                V, F = hand_builder.build_hand(pts, surf, segments=int(s.hand_segments),
                                               forearm=s.forearm_loops,
                                               knuckle_loops=s.knuckle_loops,
                                               loop_density=s.finger_loops)
        except (ValueError, np.linalg.LinAlgError) as ex:
            self.report({'ERROR'}, f"生成に失敗しました: {ex}")
            return {'CANCELLED'}

        name = f"{obj.name}_retopo_{'face' if s.kind == 'FACE' else 'hand'}"
        me = bpy.data.meshes.new(name)
        me.from_pydata([tuple(p) for p in V], [], [list(f) for f in F])
        me.validate()
        me.update()
        new = bpy.data.objects.new(name, me)
        new.matrix_world = obj.matrix_world.copy()
        context.collection.objects.link(new)
        for o in context.selected_objects:
            o.select_set(False)
        new.select_set(True)
        context.view_layer.objects.active = new
        # Ready for clean-up with the Quad Draw add-on, if installed.
        if hasattr(context.scene, "quad_draw"):
            context.scene.quad_draw.target = obj
        self.report({'INFO'}, f"{name}: {len(V)} verts, {len(F)} quads")
        return {'FINISHED'}


class OBJECT_OT_auto_retopo_clear(bpy.types.Operator):
    """Remove all guides of the current type from the active object"""
    bl_idname = "object.auto_retopo_clear"
    bl_label = "Clear Guides"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        G.save(context.active_object, _settings(context).kind, {})
        return {'FINISHED'}


classes = (
    OBJECT_OT_auto_retopo_guides,
    OBJECT_OT_auto_retopo_generate,
    OBJECT_OT_auto_retopo_clear,
)
