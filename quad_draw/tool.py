# SPDX-License-Identifier: GPL-3.0-or-later
"""The Quad Draw modal tool (Maya "Quad Draw" workflow for Blender).

Controls (identical to Maya):
    LMB                 place a dot on the live surface
    LMB drag            tweak a dot / vertex / edge / face (slides on surface)
    Shift  (hover)      preview a quad from the nearest dots / border vertices
    Shift + LMB         fill the previewed quad (or triangle)
    Shift + LMB drag    relax brush
    Ctrl   (hover)      preview an edge loop
    Ctrl + LMB (drag)   insert edge loop (drag to slide before release)
    Ctrl + MMB          insert centred edge loop
    Ctrl+Shift + LMB    delete dot / vertex / edge loop / face (drag = paint)
    Tab + LMB drag      on a border edge: extend quad strip
                        on empty surface: draw a new quad strip
    Tab + MMB drag      extend every connected border edge at once
    B + drag            resize brush
    Ctrl+Z / Ctrl+Shift+Z   undo / redo
    Esc / Enter / Q     exit tool
"""

import traceback

import bmesh
import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import drawing
from . import mesh_ops
from .surface import ReferenceSurface, project_fn, normal_fn, empty_surface_size

# Dots live outside the mesh (like Maya) and survive between tool sessions.
_DOTS = {}

DRAG_THRESHOLD = 4  # px
FILL_RADIUS = 350  # px, search radius for fill candidates

NAV_TYPES = {
    'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE',
    'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEROTATE',
    'MOUSESMARTZOOM', 'NDOF_MOTION', 'HOME', 'ACCENT_GRAVE',
    'NUMPAD_0', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4', 'NUMPAD_5',
    'NUMPAD_6', 'NUMPAD_7', 'NUMPAD_8', 'NUMPAD_9', 'NUMPAD_PERIOD',
    'NUMPAD_SLASH', 'NUMPAD_ASTERIX', 'NUMPAD_MINUS', 'NUMPAD_PLUS',
}

SHIFT_KEYS = {'LEFT_SHIFT', 'RIGHT_SHIFT'}
CTRL_KEYS = {'LEFT_CTRL', 'RIGHT_CTRL', 'OSKEY'}


def _cross2(a, b):
    return a.x * b.y - a.y * b.x


def _point_in_poly(p, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        pi, pj = poly[i], poly[j]
        if (pi.y > p.y) != (pj.y > p.y):
            x = (pj.x - pi.x) * (p.y - pi.y) / ((pj.y - pi.y) or 1e-12) + pi.x
            if p.x < x:
                inside = not inside
        j = i
    return inside


def _segments_cross(p1, p2, p3, p4):
    d1 = _cross2(p4 - p3, p1 - p3)
    d2 = _cross2(p4 - p3, p2 - p3)
    d3 = _cross2(p2 - p1, p3 - p1)
    d4 = _cross2(p2 - p1, p4 - p1)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


def _is_simple(poly):
    n = len(poly)
    if n < 4:
        return True
    for i in range(n):
        a1, a2 = poly[i], poly[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if _segments_cross(a1, a2, poly[j], poly[(j + 1) % n]):
                return False
    return True


class _Cand:
    """A fill candidate: an existing open vertex or a dot."""
    __slots__ = ("kind", "ref", "p", "co")

    def __init__(self, kind, ref, p, co):
        self.kind = kind  # 'VERT' | 'DOT'
        self.ref = ref    # BMVert | dot index
        self.p = p        # 2D Vector
        self.co = co      # local 3D Vector


class MESH_OT_quad_draw(bpy.types.Operator):
    """Maya-style Quad Draw: retopologise on top of a live reference surface"""
    bl_idname = "mesh.quad_draw"
    bl_label = "Quad Draw"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        ob = context.edit_object
        return (ob is not None and ob.type == 'MESH'
                and context.area is not None and context.area.type == 'VIEW_3D')

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------

    def invoke(self, context, event):
        self.obj = context.edit_object
        self.area = context.area
        self.region = context.region
        self.rv3d = context.region_data
        if self.region is None or self.region.type != 'WINDOW' or self.rv3d is None:
            for region in self.area.regions:
                if region.type == 'WINDOW':
                    self.region = region
                    self.rv3d = self.area.spaces.active.region_3d
                    break
        self.settings = context.scene.quad_draw
        self.mw = self.obj.matrix_world.copy()
        self.mw_inv = self.mw.inverted_safe()
        self.mw3_inv = self.mw_inv.to_3x3()

        self.surface = self._build_surface(context)
        self.bm = bmesh.from_edit_mesh(self.obj.data)
        size = self.surface.size if self.surface else empty_surface_size(self.bm)
        self.tol = size * 1e-4
        self.project = project_fn(self.surface)
        self.normal_at = normal_fn(self.surface)

        self.dots = _DOTS.setdefault(self.obj.name, [])

        self.state = 'IDLE'
        self.mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.press_mouse = self.mouse.copy()
        self.last_mouse = self.mouse.copy()
        self.shift = event.shift
        self.ctrl = event.ctrl or event.oskey
        self.tab = False
        self.b_down = False
        self.in_region = True
        self.changed = False
        self.drag = None
        self.drag_button = 'LEFTMOUSE'
        self._clear_hover()

        self.mesh_dirty = True
        self.vcos = np.zeros((0, 3))
        self.eidx = np.zeros((0, 2), dtype=np.int64)
        self.e_open = np.zeros(0, dtype=bool)
        self.retopo_bvh = None

        self.history = [self._snapshot()]
        self.redo_stack = []

        if self.settings.retopology_overlay:
            overlay = getattr(self.area.spaces.active, "overlay", None)
            if overlay is not None and hasattr(overlay, "show_retopology"):
                overlay.show_retopology = True

        args = (self, context)
        self._h3d = bpy.types.SpaceView3D.draw_handler_add(_draw_3d, args, 'WINDOW', 'POST_VIEW')
        self._h2d = bpy.types.SpaceView3D.draw_handler_add(_draw_2d, args, 'WINDOW', 'POST_PIXEL')
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Quad Draw | LMB: dot / tweak   Shift: fill · drag relax   Ctrl: edge loop   "
            "Ctrl+Shift: delete   Tab+drag: extend / strip   Tab+MMB drag: extend border   "
            "B+drag: brush   Esc/Enter/Q: exit")
        if self.surface is None:
            self.report({'WARNING'}, "Quad Draw: no live surface found, drawing on the 3D cursor plane")
        self._update_hover()
        self.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def _build_surface(self, context):
        s = context.scene.quad_draw
        if s.target is not None and s.target != self.obj:
            targets = [s.target]
        else:
            targets = [ob for ob in context.view_layer.objects
                       if ob.type == 'MESH' and ob != self.obj and ob.visible_get()]
        if not targets:
            return None
        return ReferenceSurface.from_objects(context.evaluated_depsgraph_get(), targets, self.mw_inv)

    def _finish(self, context):
        if getattr(self, "_h3d", None):
            bpy.types.SpaceView3D.draw_handler_remove(self._h3d, 'WINDOW')
            self._h3d = None
        if getattr(self, "_h2d", None):
            bpy.types.SpaceView3D.draw_handler_remove(self._h2d, 'WINDOW')
            self._h2d = None
        try:
            context.workspace.status_text_set(None)
        except Exception:
            pass
        self._free_history()
        if self.bm.is_valid:
            bmesh.update_edit_mesh(self.obj.data)
        self.area.tag_redraw()

    # ------------------------------------------------------------------
    # Mesh helpers
    # ------------------------------------------------------------------

    def _reload_bm(self):
        self.obj = bpy.context.edit_object or self.obj
        self.bm = bmesh.from_edit_mesh(self.obj.data)
        self.mesh_dirty = True

    def _ensure_cache(self):
        if not self.mesh_dirty:
            return
        bm = self.bm
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        if bm.verts:
            self.vcos = np.array([v.co[:] for v in bm.verts], dtype=np.float64)
        else:
            self.vcos = np.zeros((0, 3))
        if bm.edges:
            self.eidx = np.array([(e.verts[0].index, e.verts[1].index) for e in bm.edges],
                                 dtype=np.int64)
            self.e_open = np.array([mesh_ops.is_open_edge(e) for e in bm.edges], dtype=bool)
        else:
            self.eidx = np.zeros((0, 2), dtype=np.int64)
            self.e_open = np.zeros(0, dtype=bool)
        self.retopo_bvh = BVHTree.FromBMesh(bm) if bm.faces else None
        self.mesh_dirty = False

    def _mesh_changed(self, destructive=True):
        bmesh.update_edit_mesh(self.obj.data, loop_triangles=True, destructive=destructive)
        self.mesh_dirty = True

    def _snapshot(self):
        return (self.bm.copy(), [d.copy() for d in self.dots])

    def _restore(self, snap):
        snap_bm, dots = snap
        tmp = bpy.data.meshes.new("_quad_draw_tmp")
        try:
            snap_bm.to_mesh(tmp)
            self.bm.clear()
            self.bm.from_mesh(tmp)
        finally:
            bpy.data.meshes.remove(tmp)
        self.dots[:] = [d.copy() for d in dots]
        self._mesh_changed()

    def _blender_undo_push(self, message):
        try:
            bpy.ops.ed.undo_push(message="Quad Draw: " + message)
        except RuntimeError:
            pass

    def _push_undo(self, message):
        self._mesh_changed()
        self._blender_undo_push(message)
        self.history.append(self._snapshot())
        if len(self.history) > 200:
            self.history.pop(0)[0].free()
        for snap in self.redo_stack:
            snap[0].free()
        self.redo_stack.clear()

    def _undo(self):
        if len(self.history) <= 1:
            return
        self.redo_stack.append(self.history.pop())
        self._restore(self.history[-1])
        self._blender_undo_push("Undo")

    def _redo(self):
        if not self.redo_stack:
            return
        snap = self.redo_stack.pop()
        self.history.append(snap)
        self._restore(snap)
        self._blender_undo_push("Redo")

    def _free_history(self):
        for snap in self.history + self.redo_stack:
            snap[0].free()
        self.history = []
        self.redo_stack = []

    # ------------------------------------------------------------------
    # View helpers (everything in retopo local space)
    # ------------------------------------------------------------------

    def _ray(self, co2d):
        o = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, co2d)
        d = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, co2d)
        return self.mw_inv @ o, (self.mw3_inv @ d).normalized()

    def _view_origin(self, co):
        """Eye position (local) used for visibility tests of ``co``."""
        if self.rv3d.is_perspective:
            return self.mw_inv @ self.rv3d.view_matrix.inverted().translation
        vdir = self.rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        vdir = (self.mw3_inv @ vdir).normalized()
        size = self.surface.size if self.surface else 100.0
        return co - vdir * size * 4.0

    def _visible(self, co):
        if self.surface is None:
            return True
        # Low-poly retopo components sag below curved surfaces, so test the
        # closest surface point instead of the component itself.
        co = self.surface.nearest(co) or co
        return self.surface.is_visible(co, self._view_origin(co), self.surface.size * 0.005)

    def _hit(self, co2d):
        """Surface point under a 2D position: (loc, normal) or (None, None)."""
        o, d = self._ray(co2d)
        if self.surface is not None:
            loc, nor, _dist = self.surface.ray_cast(o, d)
            return loc, nor
        world = view3d_utils.region_2d_to_location_3d(
            self.region, self.rv3d, co2d, bpy.context.scene.cursor.location)
        return self.mw_inv @ world, None

    def _hit_or_plane(self, co2d, depth_co):
        loc, nor = self._hit(co2d)
        if loc is not None:
            return loc
        world = view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, co2d,
                                                      self.mw @ depth_co)
        return self.mw_inv @ world

    def _to2d(self, co):
        p = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, self.mw @ co)
        return p

    def _project_np(self, cos):
        if len(cos) == 0:
            return np.zeros((0, 2)), np.zeros(0, dtype=bool)
        pm = np.array([list(r) for r in (self.rv3d.perspective_matrix @ self.mw)])
        h = cos @ pm[:, :3].T + pm[:, 3]
        w = h[:, 3]
        ok = w > 1e-8
        w = np.where(ok, w, 1.0)
        x = (h[:, 0] / w * 0.5 + 0.5) * self.region.width
        y = (h[:, 1] / w * 0.5 + 0.5) * self.region.height
        return np.stack([x, y], axis=1), ok

    def _local_px_size(self, co):
        """Local-space length of one pixel at ``co``."""
        p = self._to2d(co)
        if p is None:
            return self.tol
        a = view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, p, self.mw @ co)
        b = view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, p + Vector((1, 0)),
                                                  self.mw @ co)
        return (self.mw_inv @ a - self.mw_inv @ b).length

    # ------------------------------------------------------------------
    # Picking
    # ------------------------------------------------------------------

    def _pick_vert(self, mouse, radius, exclude=(), only_open=False):
        self._ensure_cache()
        if not len(self.vcos):
            return None, 1e9
        cos = np.array([v.co[:] for v in self.bm.verts])
        xy, ok = self._project_np(cos)
        d = np.hypot(xy[:, 0] - mouse.x, xy[:, 1] - mouse.y)
        d[~ok] = np.inf
        for i in np.argsort(d)[:16]:
            if d[i] > radius:
                break
            v = self.bm.verts[i]
            if v in exclude or (only_open and not mesh_ops.is_open_vert(v)):
                continue
            if self._visible(v.co):
                return v, float(d[i])
        return None, 1e9

    def _pick_dot(self, mouse, radius, exclude=()):
        if not self.dots:
            return None, 1e9
        xy, ok = self._project_np(np.array([d[:] for d in self.dots]))
        d = np.hypot(xy[:, 0] - mouse.x, xy[:, 1] - mouse.y)
        d[~ok] = np.inf
        for i in np.argsort(d):
            if d[i] > radius:
                break
            if int(i) in exclude:
                continue
            if self._visible(self.dots[i]):
                return int(i), float(d[i])
        return None, 1e9

    def _pick_edge(self, mouse, radius, only_open=False):
        """Returns (edge, dist, t) with t measured from edge.verts[0]."""
        self._ensure_cache()
        if not len(self.eidx):
            return None, 1e9, 0.5
        xy, ok = self._project_np(self.vcos)
        a = xy[self.eidx[:, 0]]
        b = xy[self.eidx[:, 1]]
        ab = b - a
        l2 = np.maximum((ab * ab).sum(1), 1e-12)
        m = np.array([mouse.x, mouse.y])
        t = np.clip(((m - a) * ab).sum(1) / l2, 0.0, 1.0)
        proj = a + ab * t[:, None]
        d = np.hypot(proj[:, 0] - m[0], proj[:, 1] - m[1])
        d[~(ok[self.eidx[:, 0]] & ok[self.eidx[:, 1]])] = np.inf
        if only_open:
            d[~self.e_open] = np.inf
        for i in np.argsort(d)[:12]:
            if d[i] > radius:
                break
            e = self.bm.edges[i]
            mid = (e.verts[0].co + e.verts[1].co) * 0.5
            if self._visible(mid):
                return e, float(d[i]), float(t[i])
        return None, 1e9, 0.5

    def _pick_face(self, mouse):
        self._ensure_cache()
        if self.retopo_bvh is None:
            return None
        o, d = self._ray(mouse)
        loc, _nor, idx, dist = self.retopo_bvh.ray_cast(o, d)
        if loc is None:
            return None
        if not self._visible(loc):
            return None
        if idx is None or idx >= len(self.bm.faces):
            return None
        return self.bm.faces[idx]

    def _edge_t(self, edge, mouse):
        a = self._to2d(edge.verts[0].co)
        b = self._to2d(edge.verts[1].co)
        if a is None or b is None:
            return 0.5
        ab = b - a
        l2 = ab.length_squared
        if l2 < 1e-9:
            return 0.5
        return min(max((mouse - a).dot(ab) / l2, 0.02), 0.98)

    # ------------------------------------------------------------------
    # Hover state
    # ------------------------------------------------------------------

    def _clear_hover(self):
        self.h_vert = None
        self.h_dot = None
        self.h_edge = None
        self.h_face = None
        self.h_t = 0.5
        self.fill = None
        self.loop_preview = None
        self.del_target = None
        self.h_chain = None

    def _update_hover(self):
        self._clear_hover()
        if not self.in_region or self.b_down:
            return
        mouse = self.mouse
        r = self.settings.pick_radius
        if self.tab:
            self.h_edge, _d, self.h_t = self._pick_edge(mouse, r * 1.5, only_open=True)
            if self.h_edge is not None:
                verts, closed, _i = mesh_ops.border_chain(self.h_edge)
                self.h_chain = (verts, closed)
            return
        v, dv = self._pick_vert(mouse, r)
        dot, dd = self._pick_dot(mouse, r)
        if self.ctrl and not self.shift:
            e, _d, t = self._pick_edge(mouse, r)
            if e is None:
                f = self._pick_face(mouse)
                if f is not None:
                    e, t = self._nearest_face_edge(f, mouse)
            if e is not None:
                self.h_edge, self.h_t = e, t
                pts, closed = mesh_ops.edge_ring_preview(e, t)
                self.loop_preview = (pts, closed)
            return
        if self.shift and not self.ctrl:
            self.fill = self._compute_fill(mouse)
            return
        if dot is not None and dd <= dv:
            self.h_dot = dot
        elif v is not None:
            self.h_vert = v
        else:
            e, _d, t = self._pick_edge(mouse, r * 0.75)
            if e is not None:
                self.h_edge, self.h_t = e, t
            else:
                self.h_face = self._pick_face(mouse)
        if self.ctrl and self.shift:
            if self.h_dot is not None:
                self.del_target = ('DOT', self.h_dot)
            elif self.h_vert is not None:
                self.del_target = ('VERT', self.h_vert)
            elif self.h_edge is not None:
                self.del_target = ('EDGE', self.h_edge)
            elif self.h_face is not None:
                self.del_target = ('FACE', self.h_face)

    def _nearest_face_edge(self, face, mouse):
        best = (None, 1e9, 0.5)
        for e in face.edges:
            a = self._to2d(e.verts[0].co)
            b = self._to2d(e.verts[1].co)
            if a is None or b is None:
                continue
            ab = b - a
            l2 = max(ab.length_squared, 1e-9)
            t = min(max((mouse - a).dot(ab) / l2, 0.0), 1.0)
            d = (a + ab * t - mouse).length
            if d < best[1]:
                best = (e, d, min(max(t, 0.02), 0.98))
        return best[0], best[2]

    # ------------------------------------------------------------------
    # Fill (Shift)
    # ------------------------------------------------------------------

    def _fill_candidates(self, mouse):
        self._ensure_cache()
        cands = []
        if len(self.vcos):
            xy, ok = self._project_np(self.vcos)
            d = np.hypot(xy[:, 0] - mouse.x, xy[:, 1] - mouse.y)
            d[~ok] = np.inf
            count = 0
            for i in np.argsort(d):
                if d[i] > FILL_RADIUS or count >= 16:
                    break
                v = self.bm.verts[i]
                if not mesh_ops.is_open_vert(v) or not self._visible(v.co):
                    continue
                cands.append(_Cand('VERT', v, Vector(xy[i]), v.co.copy()))
                count += 1
        if self.dots:
            xy, ok = self._project_np(np.array([d[:] for d in self.dots]))
            d = np.hypot(xy[:, 0] - mouse.x, xy[:, 1] - mouse.y)
            d[~ok] = np.inf
            for i in np.argsort(d)[:16]:
                if d[i] > FILL_RADIUS:
                    break
                if self._visible(self.dots[i]):
                    cands.append(_Cand('DOT', int(i), Vector(xy[i]), self.dots[i].copy()))
        cands.sort(key=lambda c: (c.p - mouse).length)
        return cands

    def _fill_valid(self, poly):
        verts = [c.ref for c in poly if c.kind == 'VERT']
        n = len(poly)
        if n < 3:
            return False
        if len(verts) == n and self.bm.faces.get(verts) is not None:
            return False
        for i in range(n):
            a, b = poly[i], poly[(i + 1) % n]
            if a.kind == 'VERT' and b.kind == 'VERT':
                e = self.bm.edges.get((a.ref, b.ref))
                if e is not None and len(e.link_faces) >= 2:
                    return False
        pts = [c.p for c in poly]
        if not _is_simple(pts):
            return False
        # Reject degenerate (near zero area) polygons.
        area = 0.0
        for i in range(n):
            area += _cross2(pts[i], pts[(i + 1) % n])
        return abs(area) > 4.0

    def _compute_fill(self, mouse):
        cands = self._fill_candidates(mouse)
        if len(cands) < 3:
            return None

        # 1) Maya-style: extend from the nearest border edge on its open side.
        e, _d, _t = self._pick_edge(mouse, FILL_RADIUS * 0.5, only_open=True)
        if e is not None:
            a, b = e.verts
            pa, pb = self._to2d(a.co), self._to2d(b.co)
            if pa is not None and pb is not None:
                side = _cross2(pb - pa, mouse - pa)
                open_side = True
                if e.link_faces:
                    fc = e.link_faces[0].calc_center_median()
                    pc = self._to2d(fc)
                    if pc is not None and _cross2(pb - pa, pc - pa) * side > 0:
                        open_side = False
                if open_side:
                    ca = next((c for c in cands if c.kind == 'VERT' and c.ref is a), None)
                    cb = next((c for c in cands if c.kind == 'VERT' and c.ref is b), None)
                    if ca is None:
                        ca = _Cand('VERT', a, pa, a.co.copy())
                    if cb is None:
                        cb = _Cand('VERT', b, pb, b.co.copy())
                    others = [c for c in cands
                              if not (c.kind == 'VERT' and (c.ref is a or c.ref is b))
                              and _cross2(pb - pa, c.p - pa) * side > 0]
                    if len(others) >= 2:
                        x, y = others[0], others[1]
                        if (x.p - pb).length > (y.p - pb).length:
                            x, y = y, x
                        poly = [ca, cb, x, y]
                        if not _is_simple([c.p for c in poly]):
                            poly = [ca, cb, y, x]
                        if self._fill_valid(poly) and _point_in_poly(mouse, [c.p for c in poly]):
                            return poly
                    if others:
                        poly = [ca, cb, others[0]]
                        if self._fill_valid(poly) and _point_in_poly(mouse, [c.p for c in poly]):
                            return poly

        # 2) The nearest four (or three) components surrounding the cursor.
        for n in (4, 3):
            if len(cands) < n:
                continue
            pick = cands[:n]
            c2 = sum((c.p for c in pick), Vector((0.0, 0.0))) / n
            pick.sort(key=lambda c: np.arctan2(c.p.y - c2.y, c.p.x - c2.x))
            if self._fill_valid(pick) and _point_in_poly(mouse, [c.p for c in pick]):
                return pick
        return None

    def _do_fill(self, poly):
        verts = []
        used_dots = []
        for c in poly:
            if c.kind == 'VERT':
                if not c.ref.is_valid:
                    return
                verts.append(c.ref)
            else:
                verts.append(self.bm.verts.new(c.co))
                used_dots.append(c.ref)
        centre = sum((v.co for v in verts), Vector()) / len(verts)
        face = mesh_ops.create_face(self.bm, verts, self.normal_at(centre))
        if face is None:
            for c, v in zip(poly, verts):
                if c.kind == 'DOT':
                    self.bm.verts.remove(v)
            return
        for i in sorted(used_dots, reverse=True):
            self.dots.pop(i)
        if self.settings.symmetry:
            self._center_snap(verts)
            mesh_ops.symmetrize_faces(self.bm, [face], self._sym_tol(), self.dots, self.normal_at)
        self._push_undo("Fill")

    # ------------------------------------------------------------------
    # Symmetry helpers
    # ------------------------------------------------------------------

    def _sym_tol(self):
        return max(self.tol, self._local_px_size(Vector()) * 2.0) if self.region else self.tol

    def _center_snap(self, verts):
        """Snap vertices close to the symmetry plane (in pixels) onto it."""
        px = self.settings.pick_radius * 0.5
        for v in verts:
            if not v.is_valid:
                continue
            p = self._to2d(v.co)
            q = self._to2d(mesh_ops.mirror_co(v.co))
            if p is not None and q is not None and (p - q).length < px:
                v.co.x = 0.0

    def _mirror_dot_index(self, i):
        target = mesh_ops.mirror_co(self.dots[i])
        tol = self._sym_tol()
        for j, d in enumerate(self.dots):
            if j != i and (d - target).length <= tol:
                return j
        return None

    # ------------------------------------------------------------------
    # Dots
    # ------------------------------------------------------------------

    def _add_dot(self, loc):
        self.dots.append(loc.copy())
        i = len(self.dots) - 1
        mirror = None
        if self.settings.symmetry:
            p = self._to2d(loc)
            q = self._to2d(mesh_ops.mirror_co(loc))
            if p is not None and q is not None and (p - q).length < self.settings.pick_radius:
                self.dots[i].x = 0.0
                snapped = self.project(self.dots[i])
                if snapped is not None and abs(snapped.x) < self._sym_tol():
                    self.dots[i] = snapped
                self.dots[i].x = 0.0
            else:
                self.dots.append(mesh_ops.mirror_co(loc))
                mirror = len(self.dots) - 1
        return i, mirror

    # ------------------------------------------------------------------
    # Tweak (LMB drag)
    # ------------------------------------------------------------------

    def _begin_tweak(self, kind, target, new_dot=False):
        drag = {"kind": kind, "target": target, "new_dot": new_dot, "moved": False}
        start_hit = self._hit(self.press_mouse)[0]
        if kind == 'DOT':
            drag["mirror_dot"] = self._mirror_dot_index(target) if self.settings.symmetry else None
            drag["center"] = abs(self.dots[target].x) <= self._sym_tol()
        else:
            if kind == 'VERT':
                verts = [target]
            elif kind == 'EDGE':
                verts = list(target.verts)
            else:
                verts = list(target.verts)
            drag["verts"] = verts
            drag["orig"] = [v.co.copy() for v in verts]
            if start_hit is None:
                start_hit = sum((v.co for v in verts), Vector()) / len(verts)
            drag["mirror"] = {}
            if self.settings.symmetry:
                lookup = mesh_ops.MirrorLookup(self.bm, self._sym_tol())
                for v in verts:
                    m = lookup.mirror_of(v)
                    if m is not None and m not in verts:
                        drag["mirror"][v] = m
                drag["center"] = {v for v in verts if abs(v.co.x) <= self._sym_tol()}
            else:
                drag["center"] = set()
        drag["start_hit"] = start_hit
        self.drag = drag

    def _update_tweak(self):
        drag = self.drag
        drag["moved"] = True
        kind = drag["kind"]
        if kind == 'DOT':
            i = drag["target"]
            loc = self._hit_or_plane(self.mouse, self.dots[i])
            if drag["center"]:
                loc.x = 0.0
            self.dots[i] = loc
            j = drag["mirror_dot"]
            if j is not None and j < len(self.dots):
                self.dots[j] = mesh_ops.mirror_co(loc)
            return
        verts = drag["verts"]
        if kind == 'VERT':
            v = verts[0]
            loc = self._hit_or_plane(self.mouse, v.co)
            if v in drag["center"]:
                loc.x = 0.0
            v.co = loc
        else:
            start = drag["start_hit"]
            if start is None:
                return
            cur = self._hit_or_plane(self.mouse, start)
            delta = cur - start
            for v, o in zip(verts, drag["orig"]):
                co = self.project(o + delta)
                if co is None:
                    co = o + delta
                co = co.copy()
                if v in drag["center"]:
                    co.x = 0.0
                v.co = co
        for v, m in drag["mirror"].items():
            if v.is_valid and m.is_valid:
                m.co = mesh_ops.mirror_co(v.co)
        self._mesh_changed(destructive=False)

    def _end_tweak(self):
        drag = self.drag
        self.drag = None
        if drag is None:
            return
        kind = drag["kind"]
        if not drag["moved"]:
            if drag["new_dot"]:
                self._push_undo("Add Dot")
            return
        r = self.settings.pick_radius * 0.75
        if self.settings.auto_weld:
            if kind == 'DOT':
                i = drag["target"]
                p = self._to2d(self.dots[i])
                if p is not None:
                    v, _ = self._pick_vert(p, r)
                    j, _ = self._pick_dot(p, r, exclude={i})
                    if v is not None or j is not None:
                        # Dot dropped on a vertex/dot: it merges (disappears).
                        m = drag.get("mirror_dot")
                        for k in sorted({i} | ({m} if m is not None else set()), reverse=True):
                            if k < len(self.dots):
                                self.dots.pop(k)
            elif kind == 'VERT':
                v = drag["verts"][0]
                self._weld_vert(v, r)
                m = drag["mirror"].get(v)
                if m is not None and m.is_valid:
                    self._weld_vert(m, r)
        self._push_undo("Tweak")

    def _weld_vert(self, v, radius):
        """Merge ``v`` into a nearby vertex, or absorb a nearby dot."""
        if not v.is_valid:
            return v
        p = self._to2d(v.co)
        if p is None:
            return v
        self.mesh_dirty = True
        exclude = {v}
        for f in v.link_faces:
            exclude.update(f.verts)
        target, _ = self._pick_vert(p, radius, exclude=exclude)
        if target is not None:
            v = mesh_ops.merge_vert_into(self.bm, v, target)
            self.mesh_dirty = True
        j, _ = self._pick_dot(p, radius)
        if j is not None:
            self.dots.pop(j)
        return v

    # ------------------------------------------------------------------
    # Relax (Shift + drag)
    # ------------------------------------------------------------------

    def _begin_relax(self):
        self.drag = {"mirror": {}}
        if self.settings.symmetry:
            lookup = mesh_ops.MirrorLookup(self.bm, self._sym_tol())
            self.drag["mirror"] = {v: lookup.mirror_of(v) for v in self.bm.verts}

    def _update_relax(self):
        bm = self.bm
        if not bm.verts:
            return
        bm.verts.ensure_lookup_table()
        cos = np.array([v.co[:] for v in bm.verts])
        xy, ok = self._project_np(cos)
        d = np.hypot(xy[:, 0] - self.mouse.x, xy[:, 1] - self.mouse.y)
        radius = self.settings.brush_radius
        d[~ok] = np.inf
        idx = np.nonzero(d < radius)[0]
        if not len(idx):
            return
        strength = self.settings.relax_strength
        vw = []
        for i in idx:
            v = bm.verts[i]
            if not self._visible(v.co):
                continue
            f = 1.0 - (d[i] / radius) ** 2
            vw.append((v, strength * f))
        moved = mesh_ops.relax_verts(vw, self.project if self.surface else None,
                                     self.settings.relax_boundary)
        mirror = self.drag["mirror"]
        if mirror:
            moved_set = set(moved)
            for v in moved:
                m = mirror.get(v)
                if m is None or not m.is_valid:
                    continue
                if m is v:
                    v.co.x = 0.0
                elif m not in moved_set or v.co.x >= 0.0:
                    m.co = mesh_ops.mirror_co(v.co)
        self._mesh_changed(destructive=False)

    # ------------------------------------------------------------------
    # Edge loop (Ctrl)
    # ------------------------------------------------------------------

    def _insert_loop(self, edge, t):
        if edge is None or not edge.is_valid:
            return
        mirror_edge, mirror_t = None, t
        if self.settings.symmetry:
            lookup = mesh_ops.MirrorLookup(self.bm, self._sym_tol())
            ma = lookup.mirror_of(edge.verts[0])
            mb = lookup.mirror_of(edge.verts[1])
            if ma is not None and mb is not None:
                me = self.bm.edges.get((ma, mb))
                ring_edges = {e for e, _s in mesh_ops.edge_ring(edge)[0]}
                if me is not None and me not in ring_edges:
                    mirror_edge = me
                    mirror_t = t if me.verts[0] is ma else 1.0 - t
        new = mesh_ops.insert_edge_loop(self.bm, edge, t)
        if mirror_edge is not None and mirror_edge.is_valid:
            new += mesh_ops.insert_edge_loop(self.bm, mirror_edge, mirror_t)
        if self.surface is not None:
            for v in new:
                x0 = v.co.x
                p = self.project(v.co)
                if p is not None:
                    v.co = p
                    if self.settings.symmetry and abs(x0) <= self._sym_tol():
                        v.co.x = 0.0
        self._push_undo("Insert Edge Loop")

    # ------------------------------------------------------------------
    # Delete (Ctrl+Shift)
    # ------------------------------------------------------------------

    def _delete(self, target, allow_edge=True):
        if target is None:
            return False
        kind, ref = target
        bm = self.bm
        sym = self.settings.symmetry
        lookup = mesh_ops.MirrorLookup(bm, self._sym_tol()) if sym and kind != 'DOT' else None
        if kind == 'DOT':
            if ref >= len(self.dots):
                return False
            m = self._mirror_dot_index(ref) if sym else None
            for k in sorted({ref} | ({m} if m is not None else set()), reverse=True):
                self.dots.pop(k)
        elif kind == 'VERT':
            if not ref.is_valid:
                return False
            geom = [ref]
            if lookup:
                m = lookup.mirror_of(ref)
                if m is not None and m is not ref:
                    geom.append(m)
            mesh_ops.delete_verts(bm, geom)
        elif kind == 'EDGE':
            if not allow_edge or not ref.is_valid:
                return False
            mirror = None
            if lookup:
                ma, mb = lookup.mirror_of(ref.verts[0]), lookup.mirror_of(ref.verts[1])
                if ma is not None and mb is not None:
                    mirror = bm.edges.get((ma, mb))
                    if mirror is not None:
                        loop = set(mesh_ops.edge_loop(ref)) if not mesh_ops.is_open_edge(ref) \
                            else {ref}
                        if mirror in loop:
                            mirror = None
            mesh_ops.delete_edge_loop(bm, ref)
            if mirror is not None and mirror.is_valid:
                mesh_ops.delete_edge_loop(bm, mirror)
        elif kind == 'FACE':
            if not ref.is_valid:
                return False
            faces = [ref]
            if lookup:
                mv = [lookup.mirror_of(v) for v in ref.verts]
                if all(x is not None for x in mv):
                    mf = bm.faces.get(mv)
                    if mf is not None and mf is not ref:
                        faces.append(mf)
            mesh_ops.delete_faces(bm, faces)
        self._mesh_changed()
        return True

    # ------------------------------------------------------------------
    # Extend (Tab + LMB drag: one border edge / Tab + MMB drag: whole border)
    # ------------------------------------------------------------------

    def _begin_extend(self, edge, whole_border=False):
        if whole_border:
            verts, closed, grab = mesh_ops.border_chain(edge)
        else:
            verts, closed, grab = list(edge.verts), False, 0
        pairs = self._chain_pairs(len(verts), closed)
        lengths = [(verts[i].co - verts[j].co).length for i, j in pairs]
        start = self._hit(self.mouse)[0]
        if start is None:
            start = (edge.verts[0].co + edge.verts[1].co) * 0.5
        self.drag = {
            "base": verts,
            "closed": closed,
            "grab": grab,
            # LMB: the new edge follows the cursor. MMB: the whole border grows
            # outwards along each vertex's outward direction.
            "mode": 'OUTWARD' if whole_border else 'TRANSLATE',
            "row": None,  # {"verts": [...], "faces": [...]} being dragged
            "start": start,
            "spacing": max(sum(lengths) / max(len(lengths), 1), self.tol * 10),
            "faces": [],
            "new_verts": [],
            "moved": False,
            "flipped": False,
        }
        self.drag["outs"] = self._chain_outward(verts, closed)

    @staticmethod
    def _chain_pairs(n, closed):
        pairs = [(i, i + 1) for i in range(n - 1)]
        if closed and n > 2:
            pairs.append((n - 1, 0))
        return pairs

    def _chain_outward(self, verts, closed):
        """Per-vertex and per-edge outward directions (tangent to the surface)."""
        pairs = self._chain_pairs(len(verts), closed)
        edge_outs = []
        for i, j in pairs:
            a, b = verts[i], verts[j]
            mid = (a.co + b.co) * 0.5
            n = self.normal_at(mid) or Vector((0.0, 0.0, 1.0))
            e = self.bm.edges.get((a, b))
            if e is not None and e.link_faces:
                out = mid - e.link_faces[0].calc_center_median()
            else:
                out = n.cross(b.co - a.co)
            out -= n * out.dot(n)
            edge_outs.append(out.normalized() if out.length > 1e-12 else Vector())
        vert_outs = [Vector() for _ in verts]
        for (i, j), o in zip(pairs, edge_outs):
            vert_outs[i] += o
            vert_outs[j] += o
        vert_outs = [o.normalized() if o.length > 1e-12 else o for o in vert_outs]
        has_faces = any(self.bm.edges.get((verts[i], verts[j])) is not None
                        and self.bm.edges.get((verts[i], verts[j])).link_faces
                        for i, j in pairs)
        return {"verts": vert_outs, "edges": edge_outs, "has_faces": has_faces}

    def _extend_offsets(self, delta):
        drag = self.drag
        outs = drag["outs"]
        if drag["mode"] == 'TRANSLATE':
            return [delta] * len(drag["base"])
        ref = outs["edges"][min(drag["grab"], len(outs["edges"]) - 1)] if outs["edges"] \
            else Vector()
        dist = delta.dot(ref)
        if dist < 0.0 and not outs["has_faces"] and not drag["flipped"]:
            # Wire border: grow towards whichever side the user drags.
            outs["verts"] = [-o for o in outs["verts"]]
            outs["edges"] = [-o for o in outs["edges"]]
            drag["flipped"] = True
            dist = -dist
        dist = max(dist, 0.0)
        return [o * dist for o in outs["verts"]]

    def _extend_make_faces(self, base, new, closed):
        faces = []
        for i, j in self._chain_pairs(len(base), closed):
            a, b, na, nb = base[i], base[j], new[i], new[j]
            face = mesh_ops.create_face(self.bm, [a, b, nb, na],
                                        self.normal_at((a.co + nb.co) * 0.5))
            if face is not None:
                faces.append(face)
        return faces

    def _update_extend(self):
        drag = self.drag
        drag["moved"] = True
        cur = self._hit(self.mouse)[0]
        if cur is None:
            return
        base = drag["base"]
        if drag["row"] is None:
            new = [self.bm.verts.new(v.co) for v in base]
            drag["new_verts"] += new
            drag["row"] = {"verts": new, "faces": []}
        row = drag["row"]
        offsets = self._extend_offsets(cur - drag["start"])
        for v, nv, off in zip(base, row["verts"], offsets):
            co = v.co + off
            nv.co = self.project(co) or co
        dist = sum((nv.co - v.co).length for v, nv in zip(base, row["verts"])) / len(base)
        if not row["faces"] and dist > drag["spacing"] * 0.05:
            row["faces"] = self._extend_make_faces(base, row["verts"], drag["closed"])
            drag["faces"] += row["faces"]
        if row["faces"] and dist >= drag["spacing"]:
            # Commit this row and continue from its outer edges.
            drag["base"] = row["verts"]
            drag["outs"] = self._chain_outward(row["verts"], drag["closed"])
            drag["start"] = cur
            drag["row"] = None
        self._mesh_changed()

    def _discard_row(self, row):
        drag = self.drag
        for f in row["faces"]:
            if f.is_valid:
                drag["faces"].remove(f)
                self.bm.faces.remove(f)
        for v in row["verts"]:
            if v.is_valid and not v.link_faces:
                self.bm.verts.remove(v)

    def _end_extend(self):
        drag = self.drag
        if drag is None:
            return
        bm = self.bm
        if not drag["faces"]:
            # Click (no drag): extrude one row outwards by the edge length.
            if drag["row"] is not None:
                self._discard_row(drag["row"])
            self.drag = None
            base = [v for v in drag["base"] if v.is_valid]
            outs = drag["outs"]
            if len(base) != len(drag["base"]) or not outs["has_faces"]:
                self._mesh_changed()
                return
            new = []
            for v, o in zip(base, outs["verts"]):
                co = v.co + o * drag["spacing"]
                new.append(bm.verts.new(self.project(co) or co))
            faces = self._extend_make_faces(base, new, drag["closed"])
            new_verts = new
        else:
            row = drag["row"]
            if row is not None:
                base = drag["base"]
                dist = sum((nv.co - v.co).length for v, nv in zip(base, row["verts"])) / len(base)
                if not row["faces"] or dist < drag["spacing"] * 0.2:
                    self._discard_row(row)
            self.drag = None
            new_verts = [v for v in drag["new_verts"] if v.is_valid]
            faces = [f for f in drag["faces"] if f.is_valid]
        self._finalize_new_geometry(new_verts, faces)
        self._push_undo("Extend")

    def _finalize_new_geometry(self, new_verts, faces):
        self._mesh_changed()
        if self.settings.auto_weld:
            r = self.settings.pick_radius * 0.75
            new_set = set(new_verts)
            for v in new_verts:
                if not v.is_valid:
                    continue
                p = self._to2d(v.co)
                if p is None:
                    continue
                self.mesh_dirty = True
                exclude = set(new_set)
                for f in v.link_faces:
                    exclude.update(f.verts)
                target, _ = self._pick_vert(p, r, exclude=exclude, only_open=True)
                if target is not None:
                    mesh_ops.merge_vert_into(self.bm, v, target)
                    self.mesh_dirty = True
        faces = [f for f in faces if f.is_valid]
        if self.settings.symmetry and faces:
            self._center_snap([v for f in faces for v in f.verts])
            mesh_ops.symmetrize_faces(self.bm, faces, self._sym_tol(), self.dots, self.normal_at)

    # ------------------------------------------------------------------
    # Strip (Tab + drag on empty surface)
    # ------------------------------------------------------------------

    def _begin_strip(self):
        self.drag = {"samples": [self.mouse.copy()], "rows": [], "faces": [], "verts": []}

    def _strip_row(self, p, direction):
        n = Vector((-direction.y, direction.x))
        if n.length < 1e-9:
            return None
        n.normalize()
        half = self.settings.strip_width * 0.5
        left = self._hit(p + n * half)[0]
        right = self._hit(p - n * half)[0]
        if left is None or right is None:
            return None
        vl = self.bm.verts.new(left)
        vr = self.bm.verts.new(right)
        self.drag["verts"] += [vl, vr]
        return vl, vr

    def _strip_add_sample(self, p):
        drag = self.drag
        samples = drag["samples"]
        prev = samples[-1]
        direction = (p - prev)
        if direction.length < 1e-6:
            return
        direction.normalize()
        samples.append(p.copy())
        if len(drag["rows"]) == 0:
            row0 = self._strip_row(prev, direction)
            if row0 is None:
                samples.pop()
                return
            drag["rows"].append(row0)
        row = self._strip_row(p, direction)
        if row is None:
            samples.pop()
            return
        (pl, pr), (vl, vr) = drag["rows"][-1], row
        drag["rows"].append(row)
        face = mesh_ops.create_face(self.bm, [pl, pr, vr, vl],
                                    self.normal_at((pl.co + vr.co) * 0.5))
        if face is not None:
            drag["faces"].append(face)
        self._mesh_changed()

    def _update_strip(self):
        drag = self.drag
        if (self.mouse - drag["samples"][-1]).length >= self.settings.strip_width:
            self._strip_add_sample(self.mouse)

    def _end_strip(self):
        drag = self.drag
        if drag is None:
            return
        if (self.mouse - drag["samples"][-1]).length >= self.settings.strip_width * 0.5:
            self._strip_add_sample(self.mouse)
        self.drag = None
        used = {v for f in drag["faces"] if f.is_valid for v in f.verts}
        for v in drag["verts"]:
            if v.is_valid and v not in used:
                self.bm.verts.remove(v)
        verts = [v for v in drag["verts"] if v.is_valid]
        if not drag["faces"]:
            self._mesh_changed()
            return
        self._finalize_new_geometry(verts, drag["faces"])
        self._push_undo("Draw Strip")

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------

    def modal(self, context, event):
        try:
            return self._modal(context, event)
        except Exception:
            traceback.print_exc()
            self.report({'ERROR'}, "Quad Draw stopped because of an internal error (see console)")
            self._finish(context)
            return {'CANCELLED'}

    def _mouse_in_region(self, event):
        x, y = event.mouse_x, event.mouse_y
        r = self.region
        if not (r.x <= x < r.x + r.width and r.y <= y < r.y + r.height):
            return False
        for other in self.area.regions:
            if other.type in {'UI', 'TOOLS', 'HEADER', 'TOOL_HEADER', 'ASSET_SHELF',
                              'ASSET_SHELF_HEADER'} and other.width > 1 and other.height > 1:
                if other.x <= x < other.x + other.width and other.y <= y < other.y + other.height:
                    return False
        return True

    def _modal(self, context, event):
        if not self.bm.is_valid:
            self._reload_bm()
        self.area.tag_redraw()
        etype, value = event.type, event.value

        if etype in SHIFT_KEYS and value in {'PRESS', 'RELEASE'}:
            self.shift = value == 'PRESS'
        elif etype in CTRL_KEYS and value in {'PRESS', 'RELEASE'}:
            self.ctrl = value == 'PRESS'
        elif etype not in {'TIMER', 'TIMER_REPORT', 'NONE'}:
            self.shift = event.shift
            self.ctrl = event.ctrl or event.oskey

        self.mouse = Vector((event.mouse_x - self.region.x, event.mouse_y - self.region.y))
        self.in_region = self._mouse_in_region(event)

        # ---- keys -------------------------------------------------------
        if etype == 'TAB' and value in {'PRESS', 'RELEASE'}:
            if not event.is_repeat:
                self.tab = value == 'PRESS'
                if self.state == 'IDLE':
                    self._update_hover()
            return {'RUNNING_MODAL'}
        if etype == 'B' and value in {'PRESS', 'RELEASE'}:
            self.b_down = value == 'PRESS'
            self.last_mouse = self.mouse.copy()
            return {'RUNNING_MODAL'}

        if self.state == 'IDLE':
            if value == 'PRESS' and etype in {'ESC', 'RET', 'NUMPAD_ENTER'} or \
                    (etype == 'Q' and value == 'PRESS' and not (event.ctrl or event.shift or event.alt)):
                self._finish(context)
                return {'FINISHED'}
            if etype == 'Z' and value == 'PRESS' and (event.ctrl or event.oskey):
                if event.shift:
                    self._redo()
                else:
                    self._undo()
                self._update_hover()
                return {'RUNNING_MODAL'}
            if etype == 'Y' and value == 'PRESS' and event.ctrl:
                self._redo()
                self._update_hover()
                return {'RUNNING_MODAL'}
            if not self.in_region:
                if etype == 'MOUSEMOVE':
                    self._clear_hover()
                return {'PASS_THROUGH'}
            if etype == 'MIDDLEMOUSE' and value == 'PRESS' and self.tab:
                self.press_mouse = self.mouse.copy()
                self._update_hover()
                if self.h_edge is not None:
                    self._begin_extend(self.h_edge, whole_border=True)
                    self.state = 'EXTEND'
                    self.drag_button = 'MIDDLEMOUSE'
                return {'RUNNING_MODAL'}
            if etype in NAV_TYPES or etype.startswith('NDOF'):
                if etype == 'MIDDLEMOUSE' and value == 'PRESS' and self.ctrl and not self.shift:
                    self._update_hover()
                    if self.h_edge is not None:
                        self._insert_loop(self.h_edge, 0.5)
                        self._update_hover()
                        return {'RUNNING_MODAL'}
                return {'PASS_THROUGH'}
            if event.alt and etype in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}:
                return {'PASS_THROUGH'}  # Maya-style navigation keymaps
            if etype in SHIFT_KEYS or etype in CTRL_KEYS:
                self._update_hover()
                return {'RUNNING_MODAL'}
            if etype == 'MOUSEMOVE':
                if self.b_down:
                    self._resize_brush()
                else:
                    self._update_hover()
                self.last_mouse = self.mouse.copy()
                return {'RUNNING_MODAL'}
            if etype == 'LEFTMOUSE' and value == 'PRESS':
                self._on_press()
                return {'RUNNING_MODAL'}
            if etype in {'Z'} and not event.ctrl:
                return {'PASS_THROUGH'}  # shading pie
            return {'RUNNING_MODAL'}

        # ---- an interaction is in progress ------------------------------
        if etype == 'MOUSEMOVE':
            self._on_drag()
            self.last_mouse = self.mouse.copy()
            return {'RUNNING_MODAL'}
        if etype == self.drag_button and value == 'RELEASE':
            self._on_release()
            self.state = 'IDLE'
            self._update_hover()
            return {'RUNNING_MODAL'}
        if etype == 'ESC' and value == 'PRESS':
            # Finish the current interaction like a release.
            self._on_release()
            self.state = 'IDLE'
            return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _resize_brush(self):
        dx = self.mouse.x - self.last_mouse.x
        s = self.settings
        s.brush_radius = int(min(max(s.brush_radius + dx, 5), 1000))
        s.strip_width = int(min(max(s.strip_width + dx, 4), 1000))

    def _on_press(self):
        self.press_mouse = self.mouse.copy()
        self.drag_button = 'LEFTMOUSE'
        self._update_hover()
        if self.tab:
            if self.h_edge is not None:
                self._begin_extend(self.h_edge)
                self.state = 'EXTEND'
            elif self._hit(self.mouse)[0] is not None:
                self._begin_strip()
                self.state = 'STRIP'
            return
        if self.ctrl and self.shift:
            self.state = 'DELETE'
            self.drag = {"deleted": self._delete(self.del_target)}
            self._update_hover()
            return
        if self.ctrl:
            if self.h_edge is not None:
                self.drag = {"edge": self.h_edge, "t": self.h_t}
                self.state = 'LOOP'
            return
        if self.shift:
            self.drag = {"fill": self.fill}
            self.state = 'SHIFT_PRESS'
            return
        # Plain LMB
        if self.h_dot is not None:
            self._begin_tweak('DOT', self.h_dot)
        elif self.h_vert is not None:
            self._begin_tweak('VERT', self.h_vert)
        elif self.h_edge is not None:
            self._begin_tweak('EDGE', self.h_edge)
        elif self.h_face is not None:
            self._begin_tweak('FACE', self.h_face)
        else:
            loc = self._hit(self.mouse)[0]
            if loc is None:
                return
            i, _m = self._add_dot(loc)
            self._begin_tweak('DOT', i, new_dot=True)
        self.drag["pending"] = True
        self.state = 'TWEAK'

    def _on_drag(self):
        state = self.state
        dragged = (self.mouse - self.press_mouse).length >= DRAG_THRESHOLD
        if state == 'TWEAK':
            if self.drag.get("pending") and not dragged:
                return
            self.drag["pending"] = False
            self._update_tweak()
        elif state == 'SHIFT_PRESS':
            if dragged:
                self._begin_relax()
                self.state = 'RELAX'
                self._update_relax()
        elif state == 'RELAX':
            self._update_relax()
        elif state == 'LOOP':
            edge = self.drag["edge"]
            if edge.is_valid:
                self.drag["t"] = self._edge_t(edge, self.mouse)
                self.loop_preview = mesh_ops.edge_ring_preview(edge, self.drag["t"])
                self.h_edge = edge
        elif state == 'DELETE':
            self._update_hover()
            if self.del_target is not None:
                if self._delete(self.del_target, allow_edge=False):
                    self.drag["deleted"] = True
                self._update_hover()
        elif state == 'EXTEND':
            if dragged or self.drag["moved"]:
                self._update_extend()
        elif state == 'STRIP':
            self._update_strip()

    def _on_release(self):
        state = self.state
        if state == 'TWEAK':
            self._end_tweak()
        elif state == 'SHIFT_PRESS':
            fill = self._compute_fill(self.mouse) or self.drag.get("fill")
            self.drag = None
            if fill:
                self._do_fill(fill)
        elif state == 'RELAX':
            self.drag = None
            self._push_undo("Relax")
        elif state == 'LOOP':
            drag = self.drag
            self.drag = None
            self._insert_loop(drag["edge"], drag["t"])
        elif state == 'DELETE':
            deleted = self.drag and self.drag.get("deleted")
            self.drag = None
            if deleted:
                self._push_undo("Delete")
        elif state == 'EXTEND':
            self._end_extend()
        elif state == 'STRIP':
            self._end_strip()
        self.drag = None


# ---------------------------------------------------------------------------
# Draw callbacks
# ---------------------------------------------------------------------------

def _draw_3d(op, context):
    try:
        if context.area != op.area:
            return
        mw = op.mw
        drawing.begin()
        # Dots
        if op.dots:
            coords = [mw @ d for i, d in enumerate(op.dots) if i != op.h_dot]
            drawing.points(coords, drawing.COL_DOT, 9.0)
        deleting = op.del_target is not None
        col = drawing.COL_DELETE if deleting else drawing.COL_HOVER
        if op.h_dot is not None and op.h_dot < len(op.dots):
            drawing.points([mw @ op.dots[op.h_dot]], col, 13.0)
        if op.h_face is not None and op.h_face.is_valid and op.state == 'IDLE':
            pts = [mw @ v.co for v in op.h_face.verts]
            drawing.polygon(pts, drawing.COL_DELETE_FACE if deleting else drawing.COL_HOVER_FACE)
            if deleting:
                drawing.polyline(pts, drawing.COL_DELETE, 2.0, closed=True)
        if op.tab and op.h_chain and op.state == 'IDLE':
            verts, closed = op.h_chain
            if all(v.is_valid for v in verts):
                drawing.polyline([mw @ v.co for v in verts],
                                 (0.35, 0.75, 1.0, 0.45), 2.0, closed=closed)
        if op.h_edge is not None and op.h_edge.is_valid and op.loop_preview is None:
            drawing.lines([mw @ v.co for v in op.h_edge.verts],
                          drawing.COL_HOVER if not op.tab else drawing.COL_FILL_EDGE, 4.0)
        if deleting and op.del_target[0] == 'EDGE' and op.h_edge is not None and op.h_edge.is_valid:
            if mesh_ops.is_open_edge(op.h_edge):
                loop = [op.h_edge]
            else:
                loop = mesh_ops.edge_loop(op.h_edge)
            segs = []
            for e in loop:
                segs += [mw @ e.verts[0].co, mw @ e.verts[1].co]
            drawing.lines(segs, drawing.COL_DELETE, 4.0)
        if op.h_vert is not None and op.h_vert.is_valid:
            drawing.points([mw @ op.h_vert.co], col, 12.0)
        # Fill preview
        if op.fill:
            pts = [mw @ c.co for c in op.fill]
            drawing.polygon(pts, drawing.COL_FILL)
            drawing.polyline(pts, drawing.COL_FILL_EDGE, 2.0, closed=True)
            drawing.points(pts, drawing.COL_FILL_EDGE, 9.0)
        # Edge loop preview
        if op.loop_preview:
            pts, closed = op.loop_preview
            pts = [mw @ p for p in pts]
            drawing.polyline(pts, drawing.COL_LOOP, 3.0, closed=closed)
            drawing.points(pts, drawing.COL_LOOP, 6.0)
        # Strip in progress: line from last row to cursor
        if op.state == 'STRIP' and op.drag and op.drag.get("rows"):
            vl, vr = op.drag["rows"][-1]
            if vl.is_valid and vr.is_valid:
                drawing.lines([mw @ vl.co, mw @ vr.co], drawing.COL_FILL_EDGE, 3.0)
        drawing.end()
    except ReferenceError:
        pass


def _draw_2d(op, context):
    try:
        if context.area != op.area:
            return
        drawing.begin()
        s = op.settings
        m = (op.mouse.x, op.mouse.y)
        if op.b_down:
            drawing.circle_2d(m, s.brush_radius, drawing.COL_BRUSH, 2.0)
            drawing.text(m[0] + s.brush_radius + 8, m[1], [f"Brush: {s.brush_radius}px"])
        elif op.state == 'RELAX' or (op.shift and not op.ctrl and op.in_region and op.state == 'IDLE'):
            drawing.circle_2d(m, s.brush_radius, drawing.COL_RELAX, 1.5)
        elif op.tab and op.in_region and op.h_edge is None:
            drawing.circle_2d(m, s.strip_width * 0.5, drawing.COL_FILL_EDGE, 1.5)
        if s.show_hud:
            mode = "Tweak / Dot"
            if op.tab:
                mode = "Extend / Strip"
            elif op.ctrl and op.shift:
                mode = "Delete"
            elif op.ctrl:
                mode = "Edge Loop"
            elif op.shift:
                mode = "Fill / Relax"
            sym = "  [Symmetry X]" if s.symmetry else ""
            drawing.text(20, 60, [
                f"Quad Draw — {mode}{sym}",
                "LMB dot/tweak · Shift fill/relax · Ctrl loop · Ctrl+Shift delete",
                "Tab+drag extend/strip · Tab+MMB extend border · B+drag brush · Ctrl+Z undo · Esc/Q exit",
            ], size=12)
        drawing.end()
    except ReferenceError:
        pass
