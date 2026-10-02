"""Groom tools: brushes that edit guide groups directly in Object mode."""

import math

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty
from bpy_extras import view3d_utils
from mathutils import Vector

from . import binding, guides, overlay
from .sampler import BodySampler


# ======================================================================
# Editable copy of a guide group
# ======================================================================
class EditSpline:
    __slots__ = ("pts", "rad", "sel", "closed")

    def __init__(self, pts, rad, sel, closed):
        self.pts = pts
        self.rad = rad
        self.sel = sel
        self.closed = closed

    def copy(self):
        return EditSpline([p.copy() for p in self.pts], list(self.rad), self.sel, self.closed)


class GroupEdit:
    """World-space working copy of one guide group."""

    def __init__(self, obj):
        self.obj = obj
        self.kind = obj.vine_guide.kind
        self.splines = []
        self.structure = False
        mw = obj.matrix_world
        for sp in obj.data.splines:
            if sp.type == "BEZIER":
                self.structure = True  # converted to POLY on save
                pts = [mw @ bp.co for bp in sp.bezier_points]
                rad = [bp.radius for bp in sp.bezier_points]
                sel = any(bp.select_control_point for bp in sp.bezier_points)
            else:
                pts = [mw @ Vector(p.co[:3]) for p in sp.points]
                rad = [p.radius for p in sp.points]
                sel = any(p.select for p in sp.points)
            if len(pts) >= 2:
                self.splines.append(EditSpline(pts, rad, sel, sp.use_cyclic_u))
            else:
                self.structure = True

    def snapshot(self):
        return [s.copy() for s in self.splines]

    def restore(self, snap):
        self.splines = [s.copy() for s in snap]
        self.structure = True
        self.save()

    def save(self):
        obj = self.obj
        splines = obj.data.splines
        inv = obj.matrix_world.inverted()
        if not self.structure and len(splines) == len(self.splines):
            for sp, es in zip(splines, self.splines):
                if len(sp.points) != len(es.pts):
                    self.structure = True
                    break
        if self.structure or len(splines) != len(self.splines):
            splines.clear()
            for es in self.splines:
                guides.add_spline(obj, es.pts, es.rad, closed=es.closed, select=es.sel)
            self.structure = False
        else:
            for sp, es in zip(splines, self.splines):
                co = []
                for p in es.pts:
                    q = inv @ p
                    co += (q.x, q.y, q.z, 1.0)
                sp.points.foreach_set("co", co)
                sp.points.foreach_set("radius", es.rad)
                sp.points.foreach_set("select", [es.sel] * len(es.pts))
        obj.data.update_tag()

    def active_splines(self, selected_only):
        if selected_only and any(s.sel for s in self.splines):
            return [s for s in self.splines if s.sel]
        return self.splines


class Snapper:
    """Keeps body guides on the skin and skirt guides outside it."""

    def __init__(self, sampler, hover):
        self.sampler = sampler
        self.hover = hover

    def __call__(self, kind, p):
        hit = self.sampler.nearest(p)
        if hit is None:
            return p
        if kind == guides.KIND_BODY:
            return hit.loc + hit.normal * self.hover
        d = p - hit.loc
        if d.dot(hit.normal) < self.hover:
            return hit.loc + hit.normal * self.hover
        return p


# ======================================================================
# Brush operations (pure; `infl(p)` returns 0..1)
# ======================================================================
def _root_weight(i, sp):
    if sp.closed:
        return 1.0
    return min(1.0, i / 2.0)


def brush_comb(edits, infl, delta, strength, keep_length, snap, selected_only):
    changed = False
    for ge in edits:
        for sp in ge.active_splines(selected_only):
            ws = [infl(p) * _root_weight(i, sp) for i, p in enumerate(sp.pts)]
            if not any(w > 0.0 for w in ws):
                continue
            lens = [(sp.pts[i] - sp.pts[i - 1]).length for i in range(1, len(sp.pts))]
            for i, w in enumerate(ws):
                if w > 0.0:
                    sp.pts[i] = sp.pts[i] + delta(sp.pts[i]) * (w * strength)
            if keep_length and not sp.closed:
                for i in range(1, len(sp.pts)):
                    d = sp.pts[i] - sp.pts[i - 1]
                    if d.length_squared > 1e-16:
                        sp.pts[i] = sp.pts[i - 1] + d.normalized() * lens[i - 1]
            sp.pts = [snap(ge.kind, p) for p in sp.pts]
            changed = True
    return changed


def brush_smooth(edits, infl, strength, snap, selected_only):
    changed = False
    for ge in edits:
        for sp in ge.active_splines(selected_only):
            n = len(sp.pts)
            ws = [infl(p) for p in sp.pts]
            if not any(w > 0.0 for w in ws):
                continue
            old = [p.copy() for p in sp.pts]
            for i in range(n):
                if ws[i] <= 0.0:
                    continue
                if sp.closed:
                    a, b = old[i - 1], old[(i + 1) % n]
                elif 0 < i < n - 1:
                    a, b = old[i - 1], old[i + 1]
                else:
                    continue
                target = (a + b) * 0.5
                sp.pts[i] = snap(ge.kind, old[i].lerp(target, min(1.0, ws[i] * strength)))
            changed = True
    return changed


def brush_grow(edits, infl, amount, shrink, snap, selected_only):
    changed = False
    for ge in edits:
        for sp in ge.active_splines(selected_only):
            if sp.closed or len(sp.pts) < 2:
                continue
            w = infl(sp.pts[-1])
            if w <= 0.0:
                continue
            seg = sum((sp.pts[i] - sp.pts[i - 1]).length for i in range(1, len(sp.pts))) / (len(sp.pts) - 1)
            d = sp.pts[-1] - sp.pts[-2]
            if d.length_squared < 1e-16:
                continue
            dirn = d.normalized()
            step = amount * w
            if shrink:
                last = d.length - step
                if last < seg * 0.3:
                    if len(sp.pts) > 2:
                        sp.pts.pop()
                        sp.rad.pop()
                        ge.structure = True
                else:
                    sp.pts[-1] = snap(ge.kind, sp.pts[-2] + dirn * last)
            else:
                tip = snap(ge.kind, sp.pts[-1] + dirn * step)
                if (tip - sp.pts[-2]).length > seg * 1.5:
                    sp.pts.append(tip)
                    sp.rad.append(sp.rad[-1])
                    ge.structure = True
                else:
                    sp.pts[-1] = tip
            changed = True
    return changed


def brush_thickness(edits, infl, strength, shrink, selected_only):
    changed = False
    f = 1.0 + 0.08 * strength
    for ge in edits:
        for sp in ge.active_splines(selected_only):
            for i, p in enumerate(sp.pts):
                w = infl(p)
                if w > 0.0:
                    k = f ** w
                    sp.rad[i] = max(0.05, min(20.0, sp.rad[i] / k if shrink else sp.rad[i] * k))
                    changed = True
    return changed


def brush_erase(edits, infl, selected_only):
    changed = False
    for ge in edits:
        cand = set(map(id, ge.active_splines(selected_only)))
        keep = [s for s in ge.splines if id(s) not in cand or not any(infl(p) > 0.0 for p in s.pts)]
        if len(keep) != len(ge.splines):
            ge.splines = keep
            ge.structure = True
            changed = True
    return changed


def brush_select(edits, infl, deselect):
    changed = False
    for ge in edits:
        for sp in ge.splines:
            if any(infl(p) > 0.0 for p in sp.pts):
                if sp.sel == deselect:
                    sp.sel = not deselect
                    changed = True
    return changed


# ======================================================================
# Helpers shared by the modal operators
# ======================================================================
def editable_groups(body):
    return [o for o in guides.guide_objects(body) if o.vine_guide.visible and not o.vine_guide.locked]


def active_group(context, create=True):
    P = context.scene.vine_dress
    body = P.body
    objs = bpy.data.objects
    if 0 <= P.active_group_index < len(objs):
        obj = objs[P.active_group_index]
        if guides.is_guide_of(obj, body):
            return obj
    if not create or body is None:
        return None
    for o in guides.guide_objects(body):
        P.active_group_index = list(objs).index(o)
        return o
    from . import groom_tree
    obj = guides.new_group(context, body, guides.KIND_BODY, "VG_Group")
    groom_tree.tree_add_group(groom_tree.ensure_tree(context, body), obj)
    P.active_group_index = list(bpy.data.objects).index(obj)
    return obj


def ensure_rest(context, body, op):
    switched = False
    for arm in binding.body_armatures(body):
        if arm.data.pose_position != "REST":
            arm.data.pose_position = "REST"
            switched = True
    if switched:
        context.view_layer.update()
        op.report({"INFO"}, "グルーミングのためレストポーズに切り替えました（ポーズ表示は「レストポーズ切替」で戻せます）")


class ScreenBrush:
    """Screen-space brush with occlusion test against the body."""

    def __init__(self, context, sampler, radius_px, xray):
        self.region = context.region
        self.rv3d = context.region_data
        self.sampler = sampler
        self.r = radius_px
        self.xray = xray
        self.mouse = Vector((0.0, 0.0))
        self.prev = Vector((0.0, 0.0))

    def visible(self, p, co2d):
        if self.xray:
            return True
        origin = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, co2d)
        d = p - origin
        dist = d.length
        if dist < 1e-9:
            return True
        hit = self.sampler.bvh.ray_cast(origin, d / dist, dist)
        return hit[0] is None or (hit[0] - origin).length > dist - self.sampler.height * 0.01

    def infl(self, p):
        co = view3d_utils.location_3d_to_region_2d(self.region, self.rv3d, p)
        if co is None:
            return 0.0
        d = (co - self.mouse).length
        if d >= self.r:
            return 0.0
        if not self.visible(p, co):
            return 0.0
        x = d / self.r
        return (1.0 - x * x) ** 2

    def delta(self, p):
        a = view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, self.prev, p)
        b = view3d_utils.region_2d_to_location_3d(self.region, self.rv3d, self.mouse, p)
        return b - a

    def ray(self, co2d=None):
        co2d = self.mouse if co2d is None else co2d
        origin = view3d_utils.region_2d_to_origin_3d(self.region, self.rv3d, co2d)
        vec = view3d_utils.region_2d_to_vector_3d(self.region, self.rv3d, co2d)
        return origin, vec

    def surface_hit(self):
        o, v = self.ray()
        loc, nrm, _i, _d = self.sampler.bvh.ray_cast(o, v)
        return loc


def _body_and_sampler(context, op):
    from . import pipeline

    body = pipeline.get_body(context)
    if body is None:
        op.report({"ERROR"}, "人物メッシュを指定してください")
        return None, None, None
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    ensure_rest(context, body, op)
    sampler = BodySampler(context, body)
    P = context.scene.vine_dress
    hover = pipeline.guide_hover(P, pipeline.body_scale(body, P))
    return body, sampler, hover


# ======================================================================
# Brush operator
# ======================================================================
BRUSH_ITEMS = [
    ("COMB", "コーム", "ガイドをなでて流れを整える"),
    ("SMOOTH", "スムーズ", "ガイドのガタつきをならす"),
    ("GROW", "伸ばす/縮める", "ガイドの先端を伸ばす（Ctrlで縮める）"),
    ("THICK", "太さ", "ガイドの太さを上げる（Ctrlで下げる）"),
    ("ERASE", "消去", "触れたガイドを削除"),
    ("SELECT", "選択", "触れたガイドを選択（Ctrlで解除）"),
]


class VINEDRESS_OT_groom_brush(bpy.types.Operator):
    bl_idname = "vine_dress.groom_brush"
    bl_label = "グルームブラシ"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    mode: EnumProperty(items=BRUSH_ITEMS)
    invert: BoolProperty(default=False, options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        body, sampler, hover = _body_and_sampler(context, self)
        if body is None:
            return {"CANCELLED"}
        P = context.scene.vine_dress
        self.P = P
        self.edits = [GroupEdit(o) for o in editable_groups(body)]
        if not self.edits:
            self.report({"WARNING"}, "編集できるガイドグループがありません")
            return {"CANCELLED"}
        self.snaps = [ge.snapshot() for ge in self.edits]
        self.body = body
        self.snap = Snapper(sampler, hover)
        self.brush = ScreenBrush(context, sampler, P.brush_radius, P.brush_xray)
        self.brush.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.brush.prev = self.brush.mouse.copy()
        self.changed = False
        if self.mode == "SELECT" and not (event.shift or self.invert):
            for ge in self.edits:
                for sp in ge.splines:
                    if sp.sel:
                        sp.sel = False
                        self.changed = True
        overlay.brush_state.update(active=True, x=event.mouse_region_x, y=event.mouse_region_y,
                                   r=P.brush_radius, mode=self.mode, invert=self.invert)
        self._apply()
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _apply(self):
        P, b = self.P, self.brush
        sel_only = P.brush_selected_only and self.mode != "SELECT"
        m = self.mode
        if m == "COMB":
            ch = brush_comb(self.edits, b.infl, b.delta, P.brush_strength * 2.0, P.comb_keep_length,
                            self.snap, sel_only)
        elif m == "SMOOTH":
            ch = brush_smooth(self.edits, b.infl, P.brush_strength * 0.5, self.snap, sel_only)
        elif m == "GROW":
            moved = (b.mouse - b.prev).length
            if moved <= 0.0:
                return
            hit = b.surface_hit()
            ref = hit if hit is not None else self.snap.sampler.co[0]
            a = view3d_utils.region_2d_to_location_3d(b.region, b.rv3d, b.prev, ref)
            c = view3d_utils.region_2d_to_location_3d(b.region, b.rv3d, b.mouse, ref)
            ch = brush_grow(self.edits, b.infl, (c - a).length * P.brush_strength * 2.0, self.invert,
                            self.snap, sel_only)
        elif m == "THICK":
            ch = brush_thickness(self.edits, b.infl, P.brush_strength, self.invert, sel_only)
        elif m == "ERASE":
            ch = brush_erase(self.edits, b.infl, sel_only)
        else:
            ch = brush_select(self.edits, b.infl, self.invert)
        if ch:
            self.changed = True
            for ge in self.edits:
                ge.save()

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            self.brush.prev = self.brush.mouse.copy()
            self.brush.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
            overlay.brush_state.update(x=event.mouse_region_x, y=event.mouse_region_y)
            self._apply()
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._finish(context)
        if event.type in {"RIGHTMOUSE", "ESC"}:
            for ge, snap in zip(self.edits, self.snaps):
                ge.restore(snap)
            overlay.brush_state["active"] = False
            context.area.tag_redraw()
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        overlay.brush_state["active"] = False
        context.area.tag_redraw()
        if self.changed and self.mode != "SELECT":
            from . import groom_tree
            groom_tree.schedule_for_body(self.body)
        return {"FINISHED"}


# ======================================================================
# Draw operator
# ======================================================================
class VINEDRESS_OT_groom_draw(bpy.types.Operator):
    bl_idname = "vine_dress.groom_draw"
    bl_label = "ガイドを描く"
    bl_description = "体の上をドラッグしてアクティブグループに新しいガイドを描く"
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        body, sampler, hover = _body_and_sampler(context, self)
        if body is None:
            return {"CANCELLED"}
        group = active_group(context)
        if group.vine_guide.locked:
            self.report({"WARNING"}, "アクティブグループはロックされています")
            return {"CANCELLED"}
        self.group = group
        self.body = body
        self.kind = group.vine_guide.kind
        self.snap = Snapper(sampler, hover)
        P = context.scene.vine_dress
        self.brush = ScreenBrush(context, sampler, P.brush_radius, True)
        self.brush.mouse = Vector((event.mouse_region_x, event.mouse_region_y))
        self.last2d = self.brush.mouse.copy()
        hit = self.brush.surface_hit()
        if hit is None:
            if self.kind == guides.KIND_BODY:
                self.report({"WARNING"}, "体の上から描き始めてください")
                return {"CANCELLED"}
            hit = context.scene.cursor.location.copy()
        self.anchor = hit.copy()
        self.points = [self.snap(self.kind, hit)]
        overlay.stroke_state.update(active=True, points=self.points, color=tuple(group.vine_guide.color))
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            m = Vector((event.mouse_region_x, event.mouse_region_y))
            if (m - self.last2d).length < 6.0:
                return {"RUNNING_MODAL"}
            self.brush.mouse = m
            p = None
            if self.kind == guides.KIND_BODY:
                hit = self.brush.surface_hit()
                if hit is not None:
                    p = self.snap(self.kind, hit)
            else:
                b = self.brush
                p = self.snap(self.kind, view3d_utils.region_2d_to_location_3d(b.region, b.rv3d, m, self.anchor))
            if p is not None:
                self.points.append(p)
                self.last2d = m
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "LEFTMOUSE" and event.value == "RELEASE":
            return self._finish(context)
        if event.type in {"RIGHTMOUSE", "ESC"}:
            overlay.stroke_state["active"] = False
            context.area.tag_redraw()
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        from . import groom_tree, pipeline

        overlay.stroke_state["active"] = False
        context.area.tag_redraw()
        P = context.scene.vine_dress
        if len(self.points) < 2:
            return {"CANCELLED"}
        spacing = P.guide_point_spacing * pipeline.body_scale(self.body, P)
        pts, rad = guides.resample(self.points, [1.0] * len(self.points), spacing, False)
        if len(pts) < 2:
            return {"CANCELLED"}
        pts = [self.snap(self.kind, p) for p in pts]
        guides.add_spline(self.group, pts, rad)
        self.group.data.update_tag()
        groom_tree.schedule_for_body(self.body)
        return {"FINISHED"}


class VINEDRESS_OT_brush_radius(bpy.types.Operator):
    bl_idname = "vine_dress.brush_radius"
    bl_label = "ブラシ半径"
    bl_options = {"INTERNAL"}

    delta: IntProperty(default=10)

    def execute(self, context):
        P = context.scene.vine_dress
        P.brush_radius = max(5, min(500, P.brush_radius + self.delta))
        overlay.brush_state["r"] = P.brush_radius
        if context.area:
            context.area.tag_redraw()
        return {"FINISHED"}


class VINEDRESS_OT_groom_hover(bpy.types.Operator):
    bl_idname = "vine_dress.groom_hover"
    bl_label = "ブラシ表示"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        overlay.brush_state.update(hover=True, x=event.mouse_region_x, y=event.mouse_region_y,
                                   r=context.scene.vine_dress.brush_radius)
        if context.area:
            context.area.tag_redraw()
        return {"PASS_THROUGH"}


# ======================================================================
# Toolbar tools
# ======================================================================
def _common_keys():
    return (
        ("vine_dress.groom_hover", {"type": "MOUSEMOVE", "value": "ANY", "any": True}, None),
        ("vine_dress.brush_radius", {"type": "LEFT_BRACKET", "value": "PRESS", "repeat": True},
         {"properties": [("delta", -10)]}),
        ("vine_dress.brush_radius", {"type": "RIGHT_BRACKET", "value": "PRESS", "repeat": True},
         {"properties": [("delta", 10)]}),
    )


def _brush_keys(mode):
    return (
        ("vine_dress.groom_brush", {"type": "LEFTMOUSE", "value": "PRESS"},
         {"properties": [("mode", mode)]}),
        ("vine_dress.groom_brush", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True},
         {"properties": [("mode", mode), ("invert", True)]}),
        ("vine_dress.groom_brush", {"type": "LEFTMOUSE", "value": "PRESS", "shift": True},
         {"properties": [("mode", "SMOOTH" if mode != "SELECT" else "SELECT")]}),
    ) + _common_keys()


def _draw_settings(context, layout, tool, extra=()):
    P = context.scene.vine_dress
    layout.prop(P, "brush_radius")
    layout.prop(P, "brush_strength")
    for name in extra:
        layout.prop(P, name)
    layout.prop(P, "brush_selected_only")
    layout.prop(P, "brush_xray")


def _tool(idname, label, icon, desc, keymap, extra=()):
    def draw_settings(context, layout, tool):
        _draw_settings(context, layout, tool, extra)

    return type("VINEDRESS_TOOL_" + idname.split(".")[-1], (bpy.types.WorkSpaceTool,), {
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
    _tool("vine_dress.tool_draw", "ガイド描画", "ops.curve.draw",
          "体の上をドラッグして、アクティブグループにガイドを描く\n（スカート/空間グループは描き始めの奥行きの平面に描く）",
          (("vine_dress.groom_draw", {"type": "LEFTMOUSE", "value": "PRESS"}, None),) + _common_keys()),
    _tool("vine_dress.tool_comb", "コーム", "ops.curves.sculpt_comb",
          "なでてガイドの流れを整える（Shift: スムーズ）", _brush_keys("COMB"), ("comb_keep_length",)),
    _tool("vine_dress.tool_smooth", "スムーズ", "ops.curves.sculpt_smooth",
          "ガイドのガタつきをならす", _brush_keys("SMOOTH")),
    _tool("vine_dress.tool_grow", "伸ばす/縮める", "ops.curves.sculpt_grow_shrink",
          "先端を伸ばす（Ctrl: 縮める）", _brush_keys("GROW")),
    _tool("vine_dress.tool_thick", "太さ", "ops.curves.sculpt_puff",
          "ガイドの太さを上げる（Ctrl: 下げる）", _brush_keys("THICK")),
    _tool("vine_dress.tool_erase", "消去", "ops.curves.sculpt_delete",
          "触れたガイドを削除", _brush_keys("ERASE")),
    _tool("vine_dress.tool_select", "ガイド選択", "ops.generic.select_circle",
          "ブラシでガイドを選択（Shift: 追加, Ctrl: 解除）", _brush_keys("SELECT")),
]


def register_tools():
    prev = None
    for i, cls in enumerate(TOOLS):
        try:
            bpy.utils.register_tool(cls, after={prev} if prev else None, separator=(i == 0))
        except Exception as exc:  # noqa: BLE001 - toolsystem may be missing in background mode
            print("[Vine Dress] tool registration skipped:", exc)
            return
        prev = cls.bl_idname


def unregister_tools():
    for cls in reversed(TOOLS):
        try:
            bpy.utils.unregister_tool(cls)
        except Exception:  # noqa: BLE001
            pass


classes = (
    VINEDRESS_OT_groom_brush,
    VINEDRESS_OT_groom_draw,
    VINEDRESS_OT_brush_radius,
    VINEDRESS_OT_groom_hover,
)
