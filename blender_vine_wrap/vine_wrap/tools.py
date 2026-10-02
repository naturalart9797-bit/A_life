"""Object-mode tools: place guide curves point by point, and edit their points."""

import math

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector
from mathutils.geometry import interpolate_bezier

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
# Curve tool (click to place points, like Maya's CV curve tool)
# ======================================================================
HELP = ("クリック: 点を追加　Enter/Space/右クリック/ダブルクリック: 確定　"
        "Backspace: 1つ戻す　Esc: 取り消し　Alt+ドラッグ・中ボタン・ホイール: 視点操作")

_NAV_EVENTS = {"MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "WHEELINMOUSE", "WHEELOUTMOUSE",
               "TRACKPADPAN", "TRACKPADZOOM", "MOUSEROTATE", "MOUSESMARTZOOM"}


class VINEWRAP_OT_place_curve(bpy.types.Operator):
    bl_idname = "vine_wrap.place_curve"
    bl_label = "カーブを作成"
    bl_description = ("クリックで点を打ってガイドカーブを作る。対象の上をクリックすると表面に吸着、"
                      "外をクリックすると空間に置く。選択中のガイドの端から始めると延長")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if event.alt or event.oskey:
            return {"PASS_THROUGH"}  # view navigation
        target, sampler = _prepare(context, self)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        self.P, self.target, self.sampler = P, target, sampler
        self.scale = pipeline.target_scale(target, P)
        self.hover = P.guide_hover * self.scale
        self.view = View(context)
        self.region = context.region
        self.points, self.on_surface = [], []
        self.extend = None
        m = Vector((event.mouse_region_x, event.mouse_region_y))

        # Starting on an end point of the selected guide continues that guide.
        act = context.active_object
        if guides.is_guide_of(act, target) and act.select_get() and act.data.splines:
            cps = guides.control_points(act)[0][0]
            for end, p in (("END", cps[-1]), ("START", cps[0])):
                co = self.view.proj(p)
                if co is not None and (co - m).length <= PICK_PX:
                    self.extend = (act, end)
                    self.base = cps if end == "END" else list(reversed(cps))
                    break
        if self.extend is None:
            self._add(m)
        overlay.place.update(active=True, points=self._preview_base(), cursor=None)
        self._status(context)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    # ------------------------------------------------------------------
    def _locate(self, m):
        o, v = self.view.ray(m)
        hit = self.sampler.ray_cast(o, v)
        if hit is not None:
            return hit.loc + hit.normal * self.hover, True
        if self.points:
            ref = self.points[-1]
        elif self.extend is not None:
            ref = self.base[-1]
        else:
            ref = bpy.context.scene.cursor.location
        return self.view.on_plane(m, ref), False

    def _add(self, m):
        p, surf = self._locate(m)
        if self.points and (p - self.points[-1]).length < 1e-6:
            return
        self.points.append(p)
        self.on_surface.append(surf)

    def _preview_base(self):
        return (self.base if self.extend else []) + self.points

    def _status(self, context):
        n = len(self.points)
        text = "%s　［%d点%s］" % (HELP, n, "・延長中" if self.extend else "")
        context.area.header_text_set(text)
        try:
            context.workspace.status_text_set(text)
        except Exception:  # noqa: BLE001
            pass

    def _inside(self, event):
        r = self.region
        return 0 <= event.mouse_region_x < r.width and 0 <= event.mouse_region_y < r.height

    def modal(self, context, event):
        # Alt (or OS-key) + mouse is view navigation (Industry Compatible keymap,
        # "Emulate 3 Button Mouse"): always let Blender handle it.
        if event.alt or event.oskey:
            return {"PASS_THROUGH"}
        if (event.type in _NAV_EVENTS or event.type.startswith("NDOF")
                or (event.type.startswith("NUMPAD_") and event.type != "NUMPAD_ENTER")):
            return {"PASS_THROUGH"}
        if not self._inside(event) and event.type not in {"RET", "NUMPAD_ENTER", "ESC", "BACK_SPACE", "SPACE"}:
            return {"PASS_THROUGH"}
        m = Vector((event.mouse_region_x, event.mouse_region_y))
        if event.type in {"MOUSEMOVE", "INBETWEEN_MOUSEMOVE"}:
            overlay.place["cursor"] = self._locate(m)[0]
            context.area.tag_redraw()
            return {"PASS_THROUGH"}
        if event.type == "LEFTMOUSE":
            if event.value == "DOUBLE_CLICK":
                return self._finish(context)
            if event.value == "PRESS":
                self._add(m)
                overlay.place["points"] = self._preview_base()
                self._status(context)
                context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.value != "PRESS":
            return {"RUNNING_MODAL"} if event.type in {"RIGHTMOUSE", "RET", "NUMPAD_ENTER", "SPACE",
                                                        "BACK_SPACE", "ESC"} else {"PASS_THROUGH"}
        if event.type in {"RET", "NUMPAD_ENTER", "SPACE", "RIGHTMOUSE"}:
            return self._finish(context)
        if event.type == "BACK_SPACE":
            if self.points:
                self.points.pop()
                self.on_surface.pop()
            overlay.place["points"] = self._preview_base()
            self._status(context)
            context.area.tag_redraw()
            return {"RUNNING_MODAL"}
        if event.type == "ESC":
            self._end(context)
            return {"CANCELLED"}
        return {"PASS_THROUGH"}

    def _end(self, context):
        overlay.place.update(active=False, points=[], cursor=None)
        context.area.header_text_set(None)
        try:
            context.workspace.status_text_set(None)
        except Exception:  # noqa: BLE001
            pass
        context.area.tag_redraw()

    def _finish(self, context):
        self._end(context)
        if self.extend is not None:
            obj, end = self.extend
            if not self.points:
                return {"CANCELLED"}
            rad = guides.control_points(obj)[0][1]
            pts = self.base + self.points
            rads = (rad if end == "END" else list(reversed(rad))) + [rad[-1] if end == "END" else rad[0]] * len(self.points)
            if end == "START":
                pts, rads = list(reversed(pts)), list(reversed(rads))
            guides.set_spline(obj, 0, pts, rads)
        else:
            if len(self.points) < 2:
                self.report({"INFO"}, "点が2つ以上必要です")
                return {"CANCELLED"}
            snap = all(self.on_surface)
            obj = guides.new_guide(context, self.target, self.points, [1.0] * len(self.points), snap=snap,
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
                      "X・Delete: 点を削除 / Shift+ドラッグ: 太さ / 別のガイドをクリック: 選択")
    bl_options = {"REGISTER", "UNDO", "BLOCKING"}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == "VIEW_3D"

    def invoke(self, context, event):
        if event.alt or event.oskey:
            return {"PASS_THROUGH"}  # view navigation
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
# Small helpers bound to tool keymaps
# ======================================================================
class VINEWRAP_OT_hover(bpy.types.Operator):
    bl_idname = "vine_wrap.hover"
    bl_label = "ホバー"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        overlay.hover.update(x=event.mouse_region_x, y=event.mouse_region_y)
        if context.area:
            context.area.tag_redraw()
        return {"PASS_THROUGH"}


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
    _tool("vine_wrap.tool_curve", "カーブ作成", "ops.curve.draw",
          "クリックで点を打ってガイドカーブを作る（Mayaのカーブツールのように）\n"
          "対象の上: 表面に吸着 / 外: 空間に置く / 選択中ガイドの端から: 延長\n"
          "Enter・Space・右クリック・ダブルクリック: 確定 / Backspace: 1つ戻す / Esc: 取り消し",
          (("vine_wrap.place_curve", {"type": "LEFTMOUSE", "value": "PRESS"}, None), _HOVER),
          ()),
    _tool("vine_wrap.tool_edit", "ポイント編集", "ops.curve.pen",
          "点をドラッグで移動 / 線をドラッグで点を追加 / Ctrl+クリック: 端に点を追加\n"
          "X または Delete: カーソル下の点を削除 / Shift+ドラッグ: 太さ / 他のガイドをクリック: 選択\nAlt+ドラッグは視点操作のまま",
          (("vine_wrap.edit_points", {"type": "LEFTMOUSE", "value": "PRESS"}, None),
           ("vine_wrap.edit_points", {"type": "LEFTMOUSE", "value": "PRESS", "ctrl": True}, None),
           ("vine_wrap.edit_points", {"type": "LEFTMOUSE", "value": "PRESS", "shift": True}, None),
           ("vine_wrap.delete_hovered_point", {"type": "X", "value": "PRESS"}, None),
           ("vine_wrap.delete_hovered_point", {"type": "DEL", "value": "PRESS"}, None),
           _HOVER),
          ("soft_range",)),
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
    VINEWRAP_OT_place_curve,
    VINEWRAP_OT_edit_points,
    VINEWRAP_OT_delete_hovered_point,
    VINEWRAP_OT_hover,
)
