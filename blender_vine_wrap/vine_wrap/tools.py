"""Object-mode tools: draw guides, edit guide points, paint the generation density."""

import math

import bpy
from bpy.props import BoolProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.geometry import interpolate_bezier
from mathutils.kdtree import KDTree

from . import binding, guides, overlay, pipeline
from .sampler import BodySampler

PICK_PX = 12.0


# ======================================================================
# Helpers
# ======================================================================
def ensure_rest(context, target, op=None):
    switched = False
    for arm in binding.target_armatures(target):
        if arm.data.pose_position != "REST":
            arm.data.pose_position = "REST"
            switched = True
    if switched:
        context.view_layer.update()
        if op is not None:
            op.report({"INFO"}, "編集のためレストポーズに切り替えました（「レスト/ポーズ切替」で戻せます）")


class View:
    def __init__(self, context):
        self.region = context.region
        self.rv3d = context.region_data

    def proj(self, p):
        return view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, p)

    def ray(self, co2d):
        o = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, co2d)
        v = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, co2d)
        return o, v

    def on_plane(self, co2d, depth_point):
        return view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, co2d, depth_point)


def _prepare(context, op):
    target = pipeline.get_target(context)
    if target is None:
        op.report({"ERROR"}, "対象オブジェクトを設定してください")
        return None, None
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    ensure_rest(context, target, op)
    return target, BodySampler(context, target)


def visible_guides(context, target):
    vl = context.view_layer.objects
    return [o for o in guides.guide_objects(target) if o.name in vl and o.visible_get()]


def selected_or_all(context, target):
    vis = visible_guides(context, target)
    sel = [o for o in vis if o.select_get()]
    return sel or vis, bool(sel)


def make_active(context, obj):
    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    objs = list(bpy.data.objects)
    P = context.scene.vine_wrap
    idx = objs.index(obj)
    if P.active_guide_index != idx:
        P.active_guide_index = idx


def find_point(view, objs, mouse, radius=PICK_PX):
    """Nearest control point on screen -> (obj, spline_index, point_index, dist)."""
    best = None
    for obj in objs:
        for si, (pts, _r, _c) in enumerate(guides.control_points(obj)):
            for pi, p in enumerate(pts):
                co = view.proj(p)
                if co is None:
                    continue
                d = (co - mouse).length
                if d <= radius and (best is None or d < best[3]):
                    best = (obj, si, pi, d)
    return best


def find_segment(view, objs, mouse, radius=PICK_PX, samples=16):
    """Nearest point on a curve -> (obj, spline_index, segment_index, world_point, dist)."""
    best = None
    for obj in objs:
        mw = obj.matrix_world
        for si, sp in enumerate(obj.data.splines):
            if sp.type == "BEZIER":
                bps = sp.bezier_points
                n = len(bps)
                segs = n if sp.use_cyclic_u else n - 1
                for i in range(segs):
                    a, b = bps[i], bps[(i + 1) % n]
                    pts = interpolate_bezier(a.co, a.handle_right, b.handle_left, b.co, samples)
                    for q in pts[1:-1]:
                        w = mw @ q
                        co = view.proj(w)
                        if co is None:
                            continue
                        d = (co - mouse).length
                        if d <= radius and (best is None or d < best[4]):
                            best = (obj, si, i, w, d)
            else:
                ps = [mw @ Vector(p.co[:3]) for p in sp.points]
                for i in range(len(ps) - 1):
                    for k in range(1, samples - 1):
                        w = ps[i].lerp(ps[i + 1], k / (samples - 1))
                        co = view.proj(w)
                        if co is None:
                            continue
                        d = (co - mouse).length
                        if d <= radius and (best is None or d < best[4]):
                            best = (obj, si, i, w, d)
    return best


def snap_point(sampler, p, hover):
    hit = sampler.nearest(p)
    return hit.loc + hit.normal * hover if hit else p


# ======================================================================
# Draw
# ======================================================================
class VINEWRAP_OT_draw_guide(bpy.types.Operator):
    bl_idname = "vine_wrap.draw_guide"
    bl_label = "ガイドを描く"
    bl_description = "ドラッグして新しいガイドカーブを描く（Shift: 選択中のガイドを延長）"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target, sampler = _prepare(context, self)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        self.P, self.target, self.sampler = P, target, sampler
        self.scale = pipeline.target_scale(target, P)
        self.hover = P.guide_hover * self.scale
        self.view = View(context)
        m = Vector((event.mouse_region_x, event.mouse_region_y))
        self.extend = None
        if event.shift:
            act = context.active_object
            if guides.is_guide_of(act, target):
                self.extend = act
        o, v = self.view.ray(m)
        hit = sampler.ray_cast(o, v)
        if hit is not None:
            self.snap = True
            first = hit.loc + hit.normal * self.hover
        else:
            self.snap = False
            first = self.view.on_plane(m, context.scene.cursor.location)
        if self.extend is not None:
            self.snap = self.extend.vine_guide.snap
        self.anchor = first.copy()
        self.points = [first]
        self.last = m
        pipeline.hold(True)
        overlay.stroke["active"] = True
        overlay.stroke["points"] = self.points
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            if (m - self.last).length < 5.0:
                return {"RUNNING_MODAL"}
            p = None
            if self.snap:
                o, v = self.view.ray(m)
                hit = self.sampler.ray_cast(o, v)
                if hit is not None:
                    p = hit.loc + hit.normal * self.hover
            else:
                p = self.view.on_plane(m, self.anchor)
            if p is not None:
                self.points.append(p)
                self.last = m
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._finish(context)
        if event.type in {"RIGHTMOUSE", "ESC"}:
            self._end(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _end(self, context):
        overlay.stroke["active"] = False
        pipeline.hold(False)
        context.area.tag_redraw()

    def _finish(self, context):
        self._end(context)
        if len(self.points) < 2:
            return {"CANCELLED"}
        spacing = self.P.point_spacing * self.scale
        pts, rad = guides.resample(self.points, [1.0] * len(self.points), spacing)
        if len(pts) < 2:
            return {"CANCELLED"}
        if self.snap:
            pts = [snap_point(self.sampler, p, self.hover) for p in pts]
        if self.extend is not None:
            obj = self.extend
            cps, crs, _c = guides.control_points(obj)[0]
            # Attach the stroke to the nearer end of the guide.
            if (pts[0] - cps[-1]).length <= (pts[0] - cps[0]).length:
                new_p, new_r = cps + pts[1:], crs + [crs[-1]] * (len(pts) - 1)
            else:
                new_p, new_r = list(reversed(pts[1:])) + cps, [crs[0]] * (len(pts) - 1) + crs
            guides.set_spline(obj, 0, new_p, new_r)
        else:
            obj = guides.new_guide(context, self.target, pts, rad, snap=self.snap,
                                   name=guides.unique_name("Vine"))
        make_active(context, obj)
        pipeline.schedule(self.target)
        return {"FINISHED"}


# ======================================================================
# Point edit
# ======================================================================
class VINEWRAP_OT_edit_points(bpy.types.Operator):
    bl_idname = "vine_wrap.edit_points"
    bl_label = "ガイドの点を編集"
    bl_description = ("ドラッグ: 点を移動 / 線をドラッグ: 点を追加して移動 / Ctrl: 端に点を追加 / "
                      "Alt: 点を削除 / Shift+ドラッグ: 太さ / 別のガイドをクリック: 選択")
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target, sampler = _prepare(context, self)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        self.P, self.target, self.sampler = P, target, sampler
        self.scale = pipeline.target_scale(target, P)
        self.hover = P.guide_hover * self.scale
        self.view = View(context)
        m = Vector((event.mouse_region_x, event.mouse_region_y))
        self.start = m
        cands, any_sel = selected_or_all(context, target)
        if not cands:
            return {"PASS_THROUGH"}

        hitp = find_point(self.view, cands, m)
        if event.alt:
            if hitp is None:
                return {"CANCELLED"}
            self._delete_point(context, hitp)
            return {"FINISHED"}

        if hitp is None and event.ctrl:
            obj = context.active_object if guides.is_guide_of(context.active_object, target) else None
            obj = obj or (cands[0] if len(cands) == 1 else None)
            if obj is None:
                self.report({"WARNING"}, "延長するガイドを選択してください")
                return {"CANCELLED"}
            hitp = self._extend(obj, m)

        if hitp is None:
            seg = find_segment(self.view, cands, m)
            if seg is not None and (any_sel or len(cands) == 1):
                hitp = self._insert(seg)
            elif seg is None or not any_sel:
                # Click on any guide's curve: select it.
                pick = find_segment(self.view, visible_guides(context, target), m) or seg
                if pick is None:
                    pickp = find_point(self.view, visible_guides(context, target), m)
                    if pickp is None:
                        # Empty space: deselect the guides.
                        for o in context.selected_objects:
                            if guides.is_guide_of(o, target):
                                o.select_set(False)
                        return {"FINISHED"}
                    pick = (pickp[0],)
                make_active(context, pick[0])
                return {"FINISHED"}

        obj, si, pi = hitp[0], hitp[1], hitp[2]
        if not obj.select_get():
            make_active(context, obj)
        self.obj, self.si, self.pi = obj, si, pi
        pts, rad, _c = guides.control_points(obj)[si]
        self.orig_pts = [p.copy() for p in pts]
        self.orig_rad = list(rad)
        self.radius_mode = event.shift
        self.moved = False
        pipeline.hold(True)
        overlay.edit_state.update(active=True, obj=obj.name, si=si, pi=pi)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # -- structural edits ------------------------------------------------
    def _delete_point(self, context, hitp):
        obj, si, pi, _d = hitp
        pts, rad, _c = guides.control_points(obj)[si]
        if len(pts) <= 2:
            if len(obj.data.splines) <= 1:
                pipeline.remove_guide(obj)
            else:
                guides.set_spline(obj, si, pts, rad)  # keep; nothing sensible to delete
        else:
            del pts[pi]
            del rad[pi]
            guides.set_spline(obj, si, pts, rad)
        pipeline.schedule(self.target)
        context.area.tag_redraw()

    def _extend(self, obj, m):
        pts, rad, _c = guides.control_points(obj)[0]
        o, v = self.view.ray(m)
        hit = self.sampler.ray_cast(o, v) if obj.vine_guide.snap else None
        a, b = self.view.proj(pts[0]), self.view.proj(pts[-1])
        at_end = a is None or (b is not None and (b - m).length <= (a - m).length)
        ref = pts[-1] if at_end else pts[0]
        p = hit.loc + hit.normal * self.hover if hit else self.view.on_plane(m, ref)
        if at_end:
            pts.append(p)
            rad.append(rad[-1])
            idx = len(pts) - 1
        else:
            pts.insert(0, p)
            rad.insert(0, rad[0])
            idx = 0
        guides.set_spline(obj, 0, pts, rad)
        return (obj, 0, idx, 0.0)

    def _insert(self, seg):
        obj, si, i, w, _d = seg
        pts, rad, _c = guides.control_points(obj)[si]
        j = i + 1
        r = (rad[i] + rad[j % len(rad)]) * 0.5
        pts.insert(j, w)
        rad.insert(j, r)
        guides.set_spline(obj, si, pts, rad)
        return (obj, si, j, 0.0)

    # -- dragging ----------------------------------------------------------
    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            if (m - self.start).length > 2.0:
                self.moved = True
            if self.radius_mode:
                self._drag_radius(m)
            else:
                self._drag_point(m)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            self._end(context)
            pipeline.schedule(self.target)
            return {"FINISHED"}
        if event.type in {"RIGHTMOUSE", "ESC"}:
            guides.set_spline(self.obj, self.si, self.orig_pts, self.orig_rad)
            self._end(context)
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _end(self, context):
        overlay.edit_state["active"] = False
        pipeline.hold(False)
        context.area.tag_redraw()

    def _falloff(self, j):
        k = self.P.soft_range
        d = abs(j - self.pi)
        if d == 0:
            return 1.0
        if d > k:
            return 0.0
        return 0.5 * (1.0 + math.cos(math.pi * d / (k + 1)))

    def _drag_point(self, m):
        snap = self.obj.vine_guide.snap
        p0 = self.orig_pts[self.pi]
        new = None
        if snap:
            o, v = self.view.ray(m)
            hit = self.sampler.ray_cast(o, v)
            if hit is not None:
                new = hit.loc + hit.normal * self.hover
        if new is None:
            new = self.view.on_plane(m, p0)
        delta = new - p0
        pts = []
        for j, p in enumerate(self.orig_pts):
            w = self._falloff(j)
            q = p + delta * w if w > 0.0 else p.copy()
            if snap and w > 0.0 and j != self.pi:
                q = snap_point(self.sampler, q, self.hover)
            pts.append(q)
        pts[self.pi] = new
        guides.set_spline(self.obj, self.si, pts, self.orig_rad)

    def _drag_radius(self, m):
        f = math.exp((m.y - self.start.y) * 0.01)
        rad = [r * (f ** self._falloff(j)) for j, r in enumerate(self.orig_rad)]
        rad = [max(0.02, min(20.0, r)) for r in rad]
        guides.set_spline(self.obj, self.si, self.orig_pts, rad)


class VINEWRAP_OT_delete_hovered_point(bpy.types.Operator):
    bl_idname = "vine_wrap.delete_hovered_point"
    bl_label = "カーソル下の点を削除"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target = pipeline.get_target(context)
        if target is None:
            return {"CANCELLED"}
        view = View(context)
        m = Vector((event.mouse_region_x, event.mouse_region_y))
        cands, _s = selected_or_all(context, target)
        hitp = find_point(view, cands, m)
        if hitp is None:
            return {"PASS_THROUGH"}
        obj, si, pi, _d = hitp
        pts, rad, _c = guides.control_points(obj)[si]
        if len(pts) <= 2:
            pipeline.remove_guide(obj)
        else:
            del pts[pi]
            del rad[pi]
            guides.set_spline(obj, si, pts, rad)
        pipeline.schedule(target)
        context.area.tag_redraw()
        return {"FINISHED"}


# ======================================================================
# Density paint
# ======================================================================
class VINEWRAP_OT_paint(bpy.types.Operator):
    bl_idname = "vine_wrap.paint"
    bl_label = "密度をペイント"
    bl_description = "つるを生やしたい場所を塗る（Ctrl: 消す / Shift: ぼかす）"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target, sampler = _prepare(context, self)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        self.P, self.target, self.sampler = P, target, sampler
        self.view = View(context)
        vg = target.vertex_groups.get(P.paint_group)
        if vg is None:
            vg = target.vertex_groups.new(name=P.paint_group or "VineDensity")
            P.paint_group = vg.name
        self.vg = vg
        n = len(target.data.vertices)
        if n != len(sampler.co):
            self.report({"ERROR"}, "頂点数が変わるモディファイアがあるためペイントできません")
            return {"CANCELLED"}
        w = [0.0] * n
        gi = vg.index
        for v in target.data.vertices:
            for g in v.groups:
                if g.group == gi:
                    w[v.index] = g.weight
                    break
        self.w = w
        kd = KDTree(n)
        for i, c in enumerate(sampler.co):
            kd.insert(c, i)
        kd.balance()
        self.kd = kd
        self.mode = "ERASE" if event.ctrl else ("SMOOTH" if event.shift else "ADD")
        self.last = None
        overlay.brush.update(active=True, x=event.mouse_region_x, y=event.mouse_region_y, r=P.brush_radius,
                             mode=self.mode)
        self._dab(Vector((event.mouse_region_x, event.mouse_region_y)))
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _dab(self, m):
        if self.last is not None and (m - self.last).length < self.P.brush_radius * 0.2:
            return
        self.last = m
        o, v = self.view.ray(m)
        hit = self.sampler.ray_cast(o, v)
        if hit is None:
            return
        edge = self.view.on_plane(m + Vector((self.P.brush_radius, 0.0)), hit.loc)
        R = (edge - hit.loc).length
        if R <= 0.0:
            return
        found = self.kd.find_range(hit.loc, R)
        vn = self.sampler.vnormals
        st = self.P.brush_strength
        changed = {}
        if self.mode == "SMOOTH" and found:
            avg = sum(self.w[i] for _c, i, _d in found) / len(found)
        for _co, i, d in found:
            if vn[i].dot(v) > 0.3:  # facing away from the view: back side
                continue
            x = d / R
            f = (1.0 - x * x) ** 2 * st
            old = self.w[i]
            if self.mode == "ERASE":
                new = max(0.0, old - f)
            elif self.mode == "SMOOTH":
                new = old + (avg - old) * min(1.0, f * 2.0)
            else:
                new = min(1.0, old + f)
            if abs(new - old) > 1e-4:
                self.w[i] = new
                changed[i] = new
        if not changed:
            return
        by_w = {}
        zero = []
        for i, wv in changed.items():
            if wv <= 1e-4:
                zero.append(i)
            else:
                by_w.setdefault(round(wv, 3), []).append(i)
        for wv, idx in by_w.items():
            self.vg.add(idx, wv, "REPLACE")
        if zero:
            self.vg.remove(zero)
        overlay.paint_changed()

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            overlay.brush.update(x=m.x, y=m.y)
            self._dab(m)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            overlay.brush["active"] = False
            self.target.data.update()
            context.area.tag_redraw()
            return {"FINISHED"}
        return {"RUNNING_MODAL"}


# ======================================================================
# Small helpers bound to tool keymaps
# ======================================================================
class VINEWRAP_OT_hover(bpy.types.Operator):
    bl_idname = "vine_wrap.hover"
    bl_label = "ホバー"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        overlay.brush.update(hover=True, x=event.mouse_region_x, y=event.mouse_region_y,
                             r=context.scene.vine_wrap.brush_radius)
        overlay.hover.update(x=event.mouse_region_x, y=event.mouse_region_y)
        if context.area:
            context.area.tag_redraw()
        return {"PASS_THROUGH"}


class VINEWRAP_OT_brush_radius(bpy.types.Operator):
    bl_idname = "vine_wrap.brush_radius"
    bl_label = "ブラシ半径"
    bl_options = {"INTERNAL"}

    delta: IntProperty(default=10)

    def execute(self, context):
        P = context.scene.vine_wrap
        P.brush_radius = max(5, min(500, P.brush_radius + self.delta))
        overlay.brush["r"] = P.brush_radius
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


# ======================================================================
# Toolbar
# ======================================================================
_HOVER = ("vine_wrap.hover", {"type": "MOUSEMOVE", "value": "ANY", "any": True}, None)


def _tool(idname, label, icon, desc, keymap, props):
    def draw_settings(context, layout, tool):
        P = context.scene.vine_wrap
        for name in props:
            layout.prop(P, name)

    return type("VINEWRAP_TOOL_" + idname.split(".")[-1], (bpy.types.WorkSpaceTool,), {
        "bl_space_type": "VIEW_3D",
        "bl_context_mode": "OBJECT",
        "bl_idname": idname,
        "bl_label": label,
        "bl_description": desc,
        "bl_icon": icon,
        "bl_keymap": keymap,
        "draw_settings": staticmethod(draw_settings),
    })


TOOLS = [
    _tool("vine_wrap.tool_draw", "ガイド描画", "ops.curve.draw",
          "ドラッグでガイドカーブを描く。対象の上から描くと表面に吸着、外から描くと空間に描く\n"
          "Shift+ドラッグ: 選択中のガイドを延長",
          (("vine_wrap.draw_guide", {"type": "LEFTMOUSE", "value": "PRESS", "any": True}, None), _HOVER),
          ("point_spacing",)),
    _tool("vine_wrap.tool_edit", "ポイント編集", "ops.curve.pen",
          "点をドラッグで移動 / 線をドラッグで点を追加 / Ctrl+クリック: 端に点を追加\n"
          "Alt+クリック または X: 点を削除 / Shift+ドラッグ: 太さ / 他のガイドをクリック: 選択",
          (("vine_wrap.edit_points", {"type": "LEFTMOUSE", "value": "PRESS", "any": True}, None),
           ("vine_wrap.delete_hovered_point", {"type": "X", "value": "PRESS"}, None),
           ("vine_wrap.delete_hovered_point", {"type": "DEL", "value": "PRESS"}, None),
           _HOVER),
          ("soft_range",)),
    _tool("vine_wrap.tool_paint", "密度ペイント", "brush.paint_weight.draw",
          "つるを自動生成したい場所を塗る（Ctrl: 消す / Shift: ぼかす / [ ]: 半径）",
          (("vine_wrap.paint", {"type": "LEFTMOUSE", "value": "PRESS", "any": True}, None),
           ("vine_wrap.brush_radius", {"type": "LEFT_BRACKET", "value": "PRESS", "repeat": True},
            {"properties": [("delta", -10)]}),
           ("vine_wrap.brush_radius", {"type": "RIGHT_BRACKET", "value": "PRESS", "repeat": True},
            {"properties": [("delta", 10)]}),
           _HOVER),
          ("brush_radius", "brush_strength")),
]


def register_tools():
    prev = None
    for i, cls in enumerate(TOOLS):
        try:
            bpy.utils.register_tool(cls, after={prev} if prev else None, separator=(i == 0))
        except Exception as exc:  # noqa: BLE001
            print("[Vine Wrap] tool registration skipped:", exc)
            return
        prev = cls.bl_idname


def unregister_tools():
    for cls in reversed(TOOLS):
        try:
            bpy.utils.unregister_tool(cls)
        except Exception:  # noqa: BLE001
            pass


classes = (
    VINEWRAP_OT_draw_guide,
    VINEWRAP_OT_edit_points,
    VINEWRAP_OT_delete_hovered_point,
    VINEWRAP_OT_paint,
    VINEWRAP_OT_hover,
    VINEWRAP_OT_brush_radius,
)
