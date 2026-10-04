"""Tools for the density points: scatter over the surface, and one brush that
paints density / adds points / removes points (Yeti-style)."""

import math
import random

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from . import binding, pipeline, points
from .sampler import BodySampler

_NAV = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
        "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM"}


def _target(op, context):
    t = pipeline.get_target(context)
    if t is None:
        op.report({"ERROR"}, "対象オブジェクトを設定してください")
    return t


def _rest(context, target):
    for arm in binding.target_armatures(target):
        arm.data.pose_position = "REST"
    context.view_layer.update()


def _redraw(context):
    for area in context.screen.areas if context.screen else ():
        if area.type == "VIEW_3D":
            area.tag_redraw()


class VINEGROW_OT_points_scatter(bpy.types.Operator):
    bl_idname = "vine_grow.points_scatter"
    bl_label = "全体に点を散布"
    bl_description = "対象の表面全体に、同じ間隔で点を打つ（塗った密度は近くの新しい点に引き継ぐ）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        P = context.scene.vine_grow
        _rest(context, target)
        sampler = BodySampler(context, target)
        spacing = P.point_spacing * pipeline.target_scale(target, P)
        old = points.get(target)
        ps = points.scatter(sampler, target, spacing, random.Random(P.seed), old, P.point_default)
        if not len(ps):
            self.report({"ERROR"}, "点を打てませんでした（間隔が大きすぎる？）")
            return {"CANCELLED"}
        points.commit(context, target, ps)
        P.show_points = True
        _redraw(context)
        self.report({"INFO"}, "点 %d 個" % len(ps))
        return {"FINISHED"}


class VINEGROW_OT_points_clear(bpy.types.Operator):
    bl_idname = "vine_grow.points_clear"
    bl_label = "点をすべて削除"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        points.clear(target)
        _redraw(context)
        return {"FINISHED"}


class VINEGROW_OT_points_fill(bpy.types.Operator):
    bl_idname = "vine_grow.points_fill"
    bl_label = "全部の点を塗る"
    bl_description = "全部の点の密度を、ブラシの値にする"
    bl_options = {"REGISTER", "UNDO"}

    value: bpy.props.FloatProperty(default=-1.0)

    def execute(self, context):
        target = _target(self, context)
        ps = points.get(target) if target else None
        if ps is None:
            return {"CANCELLED"}
        v = context.scene.vine_grow.brush_value if self.value < 0.0 else self.value
        ps.val = [v] * len(ps)
        ps.changed()
        points.commit(context, target, ps)
        _redraw(context)
        return {"FINISHED"}


class VINEGROW_OT_brush(bpy.types.Operator):
    bl_idname = "vine_grow.brush"
    bl_label = "ブラシ"
    bl_description = ("左ドラッグ: 塗る / 点を追加 / 点を削除。Ctrl: 減らす（追加ブラシでは削除）、Shift: ぼかす。"
                      "B を押したまま上下: ブラシの大きさ。1/2/3: 塗る/追加/削除。Enter/右クリック/Esc: 終了")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        _rest(context, target)
        self.target = target
        self.sampler = BodySampler(context, target)
        self.ps = points.get(target) or points.PointSet()
        self.mw = target.matrix_world.copy()
        self.inv = self.mw.inverted()
        self.rng = random.Random()
        self.stroke = False
        self.mouse = (event.mouse_region_x, event.mouse_region_y)
        self.resize_at = None  # B held: (x, y) where resizing started
        self.resize_from = (0, 0)
        P = context.scene.vine_grow
        P.show_points = True
        self._draw = bpy.types.SpaceView3D.draw_handler_add(self._draw_circle, (context,), "WINDOW", "POST_PIXEL")
        self._header(context)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # ------------------------------------------------------------------
    def _header(self, context):
        P = context.scene.vine_grow
        name = {"PAINT": "塗る", "ADD": "点を追加", "REMOVE": "点を削除"}[P.brush_tool]
        context.area.header_text_set(
            "ブラシ: %s（値 %.3g）  左ドラッグ: 適用  Ctrl: 減らす/削除  Shift: ぼかす  B+上下: 大きさ  "
            "1/2/3: 塗る/追加/削除  Alt・中ボタン: 視点  Enter/右クリック/Esc: 終了" % (name, P.brush_value))

    def _draw_circle(self, context):
        try:
            import gpu
            from gpu_extras.batch import batch_for_shader
            shader = None
            for nm in ("UNIFORM_COLOR", "2D_UNIFORM_COLOR"):
                try:
                    shader = gpu.shader.from_builtin(nm)
                    break
                except (ValueError, KeyError):
                    continue
            if shader is None:
                return
            P = context.scene.vine_grow
            r = P.brush_radius
            x, y = self.resize_at if self.resize_at else self.mouse
            pts = [(x + math.cos(a) * r, y + math.sin(a) * r) for a in (math.tau * k / 48 for k in range(48))]
            batch = batch_for_shader(shader, "LINE_LOOP", {"pos": pts})
            shader.bind()
            col = {"PAINT": points.ramp(P.brush_value / points.VALUE_MAX),
                   "ADD": (0.9, 0.9, 0.9), "REMOVE": (1.0, 0.3, 0.3)}[P.brush_tool]
            shader.uniform_float("color", (col[0], col[1], col[2], 1.0))
            gpu.state.line_width_set(2.0)
            batch.draw(shader)
            gpu.state.line_width_set(1.0)
        except ReferenceError:
            pass

    def _finish(self, context):
        if self._draw is not None:
            bpy.types.SpaceView3D.draw_handler_remove(self._draw, "WINDOW")
            self._draw = None
        context.area.header_text_set(None)
        if self.ps.dirty:
            points.commit(context, self.target, self.ps)
        _redraw(context)

    # ------------------------------------------------------------------
    def _hit(self, context):
        region, rv3d = context.region, context.region_data
        m = Vector(self.mouse)
        o = view3d_utils.region_2d_to_origin_3d(region, rv3d, m)
        v = view3d_utils.region_2d_to_vector_3d(region, rv3d, m)
        hit = self.sampler.ray_cast(o, v)
        if hit is None:
            return None, 0.0, v
        P = context.scene.vine_grow
        edge = view3d_utils.region_2d_to_location_3d(region, rv3d, m + Vector((P.brush_radius, 0.0)), hit.loc)
        return hit, (edge - hit.loc).length, v

    def _apply(self, context, event):
        P = context.scene.vine_grow
        hit, R, view = self._hit(context)
        if hit is None or R <= 0.0:
            return
        ps = self.ps
        sc = max(self.mw.to_scale()) or 1.0
        c_loc = self.inv @ hit.loc
        R_loc = R / sc
        tool = P.brush_tool
        if tool == "ADD" and event.ctrl:
            tool = "REMOVE"
        rot = self.inv.to_3x3()
        if tool == "ADD":
            spacing = P.point_spacing * pipeline.target_scale(self.target, P) / sc
            kd = ps.kd() if len(ps) else None
            added = []
            tries = int(max(4, (R_loc / max(spacing, 1e-9)) ** 2 * 2))
            for _ in range(tries):
                a = self.rng.uniform(0.0, math.tau)
                rr = R * math.sqrt(self.rng.random())
                t1 = hit.normal.orthogonal().normalized()
                t2 = hit.normal.cross(t1)
                h = self.sampler.nearest(hit.loc + (t1 * math.cos(a) + t2 * math.sin(a)) * rr)
                if h is None or h.normal.dot(view) > 0.2:
                    continue
                p = self.inv @ h.loc
                if kd is not None and kd.find(p)[2] is not None and kd.find(p)[2] < spacing * 0.9:
                    continue
                if any((p - q).length < spacing * 0.9 for q in added):
                    continue
                added.append(p)
                ps.co.append(p)
                ps.nrm.append((rot @ h.normal).normalized())
                ps.val.append(P.brush_value if P.add_with_value else P.point_default)
            if added:
                ps.changed(structure=True)
            return
        if not len(ps):
            return
        kd = ps.kd()
        found = kd.find_range(c_loc, R_loc)
        if not found:
            return
        nloc = (rot @ hit.normal).normalized()
        if tool == "REMOVE":
            dead = {i for _co, i, _d in found if ps.nrm[i].dot(nloc) > -0.2}
            if dead:
                keep = [i for i in range(len(ps)) if i not in dead]
                ps.co = [ps.co[i] for i in keep]
                ps.nrm = [ps.nrm[i] for i in keep]
                ps.val = [ps.val[i] for i in keep]
                ps.changed(structure=True)
            return
        st = P.brush_strength
        smooth = event.shift
        lower = event.ctrl
        rad_n = ps.spacing() * 2.2
        changed = False
        for _co, i, d in found:
            if ps.nrm[i].dot(nloc) < -0.2:
                continue  # other side of a thin part
            w = (1.0 - (d / R_loc) ** 2) ** 2 * st
            v = ps.val[i]
            if smooth:
                near = kd.find_range(ps.co[i], rad_n)
                avg = sum(ps.val[j] for _c, j, _dd in near) / len(near)
                nv = v + (avg - v) * w
            elif lower:
                nv = v + (0.0 - v) * w
            else:
                nv = v + (P.brush_value - v) * w
            if abs(nv - v) > 1e-6:
                ps.val[i] = nv
                changed = True
        if changed:
            ps.changed()

    # ------------------------------------------------------------------
    def modal(self, context, event):
        P = context.scene.vine_grow
        if event.type == "B":
            # hold B and move the mouse up / down to resize the brush
            if event.value == "PRESS" and not event.is_repeat and self.resize_at is None:
                self.resize_at = self.mouse
                self.resize_from = (event.mouse_region_y, P.brush_radius)
            elif event.value == "RELEASE":
                self.resize_at = None
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            self.mouse = (event.mouse_region_x, event.mouse_region_y)
            if self.resize_at is not None:
                y0, r0 = self.resize_from
                P.brush_radius = max(2, min(1000, int(r0 * math.exp((event.mouse_region_y - y0) / 200.0))))
                context.area.tag_redraw()
                return {"RUNNING_MODAL"}
            if self.stroke:
                self._apply(context, event)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.alt or event.oskey or event.type in _NAV or event.type.startswith("NDOF"):
            return {"PASS_THROUGH"}
        if event.type in {"RET", "NUMPAD_ENTER", "ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            self._finish(context)
            return {"FINISHED"}
        if event.type == "LEFTMOUSE":
            if self.resize_at is not None:
                return {"RUNNING_MODAL"}
            if event.value == "PRESS":
                region = context.region
                self.mouse = (event.mouse_region_x, event.mouse_region_y)
                if not (0 <= self.mouse[0] < region.width and 0 <= self.mouse[1] < region.height):
                    return {"PASS_THROUGH"}
                self.stroke = True
                self._apply(context, event)
                context.area.tag_redraw()
            elif event.value == "RELEASE" and self.stroke:
                self.stroke = False
                points.commit(context, self.target, self.ps)
            return {"RUNNING_MODAL"}
        if event.value == "PRESS":
            if event.type == "LEFT_BRACKET":
                P.brush_radius = max(5, int(P.brush_radius / 1.15))
            elif event.type == "RIGHT_BRACKET":
                P.brush_radius = min(1000, int(P.brush_radius * 1.15) + 1)
            elif event.type in {"ONE", "NUMPAD_1"}:
                P.brush_tool = "PAINT"
            elif event.type in {"TWO", "NUMPAD_2"}:
                P.brush_tool = "ADD"
            elif event.type in {"THREE", "NUMPAD_3"}:
                P.brush_tool = "REMOVE"
            else:
                return {"PASS_THROUGH"}
            self._header(context)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        return {"PASS_THROUGH"}


classes = (VINEGROW_OT_points_scatter, VINEGROW_OT_points_clear, VINEGROW_OT_points_fill, VINEGROW_OT_brush)
