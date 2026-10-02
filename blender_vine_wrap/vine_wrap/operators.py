import time
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import binding, guides, pipeline
from .sampler import BodySampler
from .tools import ensure_rest, make_active


def _target(op, context):
    t = pipeline.get_target(context)
    if t is None:
        op.report({"ERROR"}, "対象オブジェクトを設定してください")
    return t


def _selected_guides(context, target):
    return [o for o in context.selected_objects if guides.is_guide_of(o, target)]


def _object_mode(context):
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


# ======================================================================
# Target / build
# ======================================================================
class VINEWRAP_OT_set_target(bpy.types.Operator):
    bl_idname = "vine_wrap.set_target"
    bl_label = "選択中を対象に設定"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "メッシュオブジェクトを選択してください")
            return {"CANCELLED"}
        context.scene.vine_wrap.target = obj
        guides.ensure_style(context.scene.vine_wrap)
        return {"FINISHED"}


class VINEWRAP_OT_build(bpy.types.Operator):
    bl_idname = "vine_wrap.build"
    bl_label = "つるを生成"
    bl_description = "すべてのガイドからつる・葉・巻きひげのメッシュを作る"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        _object_mode(context)
        obj = pipeline.run_build(context, target, self.report)
        return {"FINISHED"} if obj is not None else {"CANCELLED"}


class VINEWRAP_OT_clear(bpy.types.Operator):
    bl_idname = "vine_wrap.clear"
    bl_label = "つるメッシュを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        pipeline.remove_generated(target)
        return {"FINISHED"}


class VINEWRAP_OT_toggle_rest(bpy.types.Operator):
    bl_idname = "vine_wrap.toggle_rest"
    bl_label = "レスト/ポーズ切替"
    bl_description = "対象のアーマチュアをレストポーズ/ポーズで切り替える（ガイドはレストポーズ基準。ポーズ中はガイドを隠す）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        arms = binding.target_armatures(target)
        if not arms:
            self.report({"INFO"}, "アーマチュアがありません")
            return {"CANCELLED"}
        to_pose = arms[0].data.pose_position == "REST"
        for arm in arms:
            arm.data.pose_position = "POSE" if to_pose else "REST"
        for o in guides.guide_objects(target):
            if o.name in context.view_layer.objects:
                o.hide_set(to_pose)
        return {"FINISHED"}


# ======================================================================
# Auto generation
# ======================================================================
class VINEWRAP_OT_generate(bpy.types.Operator):
    bl_idname = "vine_wrap.generate"
    bl_label = "ペイント範囲に生成"
    bl_description = "塗った範囲に、密度に応じてガイドカーブを自動生成する（生成後は自由に編集できる）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        _object_mode(context)
        t0 = time.time()
        try:
            n = pipeline.generate(self.report, context, target)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "自動生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        if n == 0:
            self.report({"WARNING"}, "ガイドが生成されませんでした（ペイント・密度・しきい値を確認）")
        else:
            self.report({"INFO"}, "ガイド %d 本を生成しました (%.1f秒)" % (n, time.time() - t0))
        pipeline.schedule(target)
        return {"FINISHED"}


class VINEWRAP_OT_clear_auto(bpy.types.Operator):
    bl_idname = "vine_wrap.clear_auto"
    bl_label = "自動生成ガイドを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        for o in guides.guide_objects(target):
            if o.vine_guide.auto:
                pipeline.remove_guide(o)
        pipeline.schedule(target)
        return {"FINISHED"}


class VINEWRAP_OT_clear_paint(bpy.types.Operator):
    bl_idname = "vine_wrap.clear_paint"
    bl_label = "ペイントを消去"
    bl_options = {"REGISTER", "UNDO"}

    fill: BoolProperty(name="全体を塗る", default=False, options={"SKIP_SAVE"})

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        vg = target.vertex_groups.get(P.paint_group) or target.vertex_groups.new(name=P.paint_group)
        idx = list(range(len(target.data.vertices)))
        if self.fill:
            vg.add(idx, 1.0, "REPLACE")
        else:
            vg.remove(idx)
        from . import overlay
        overlay.paint_changed()
        return {"FINISHED"}


# ======================================================================
# Guides
# ======================================================================
class VINEWRAP_OT_guide_select(bpy.types.Operator):
    bl_idname = "vine_wrap.guide_select"
    bl_label = "ガイドを選択"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[
        ("ALL", "全選択", ""), ("NONE", "選択解除", ""), ("INVERT", "反転", ""),
        ("NAME", "名前で選択", "絞り込みの文字を含むガイドを選択"),
        ("AUTO", "自動生成を選択", ""), ("STYLE", "スタイルで選択", "アクティブスタイルのガイドを選択"),
    ])

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        _object_mode(context)
        P = context.scene.vine_wrap
        key = P.guide_filter.lower()
        st = guides.active_style(P)
        last = None
        for o in guides.guide_objects(target):
            if o.name not in context.view_layer.objects:
                continue
            cur = o.select_get()
            if self.action == "ALL":
                new = True
            elif self.action == "NONE":
                new = False
            elif self.action == "INVERT":
                new = not cur
            elif self.action == "NAME":
                new = bool(key) and key in o.name.lower()
            elif self.action == "AUTO":
                new = o.vine_guide.auto
            else:
                new = o.vine_guide.style_uid == st.uid
            if new:
                o.hide_set(False)
                last = o
            o.select_set(new)
        if last is not None and not (context.active_object and context.active_object.select_get()):
            context.view_layer.objects.active = last
        return {"FINISHED"}


class VINEWRAP_OT_guide_delete(bpy.types.Operator):
    bl_idname = "vine_wrap.guide_delete"
    bl_label = "選択ガイドを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        sel = _selected_guides(context, target)
        for o in sel:
            pipeline.remove_guide(o)
        self.report({"INFO"}, "%d 本削除しました" % len(sel))
        pipeline.schedule(target)
        return {"FINISHED"}


class VINEWRAP_OT_guide_duplicate(bpy.types.Operator):
    bl_idname = "vine_wrap.guide_duplicate"
    bl_label = "選択ガイドを複製"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        new = []
        for o in _selected_guides(context, target):
            c = o.copy()
            c.data = o.data.copy()
            c.name = guides.unique_name(o.name.rsplit("_", 1)[0] if "_" in o.name else o.name)
            c.vine_guide.auto = False
            for coll in o.users_collection:
                coll.objects.link(c)
            new.append(c)
        for o in context.selected_objects:
            o.select_set(False)
        for c in new:
            c.select_set(True)
        if new:
            context.view_layer.objects.active = new[-1]
        pipeline.schedule(target)
        return {"FINISHED"}


class VINEWRAP_OT_guide_edit(bpy.types.Operator):
    bl_idname = "vine_wrap.guide_edit"
    bl_label = "ガイドを整える"
    bl_description = "選択中のガイドに処理をかける"
    bl_options = {"REGISTER", "UNDO"}

    action: EnumProperty(items=[
        ("SNAP", "表面に吸着", "制御点を対象の表面に戻す"),
        ("SMOOTH", "なめらかに", "制御点をならす"),
        ("RESAMPLE", "点を均等に", "「点の間隔」で制御点を打ち直す"),
        ("REVERSE", "向きを反転", "根元と先端を入れ替える"),
        ("RESET_RADIUS", "太さをリセット", "制御点の太さを1に戻す"),
    ])

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        _object_mode(context)
        sel = _selected_guides(context, target)
        if not sel:
            self.report({"WARNING"}, "ガイドが選択されていません")
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        ensure_rest(context, target, self)
        scale = pipeline.target_scale(target, P)
        hover = P.guide_hover * scale
        sampler = BodySampler(context, target) if self.action in {"SNAP", "SMOOTH", "RESAMPLE"} else None
        for o in sel:
            snap = o.vine_guide.snap
            for si, (pts, rad, cyc) in enumerate(guides.control_points(o)):
                if self.action == "SNAP":
                    pts = [_snap(sampler, p, hover) for p in pts]
                elif self.action == "SMOOTH":
                    n = len(pts)
                    for _ in range(2):
                        pts = [pts[i] if (not cyc and i in (0, n - 1)) else
                               (pts[i - 1] + pts[i] * 2.0 + pts[(i + 1) % n]) * 0.25 for i in range(n)]
                    if snap:
                        pts = [_snap(sampler, p, hover) for p in pts]
                elif self.action == "RESAMPLE":
                    dense = guides.evaluated(o)[si]
                    pts, rad = guides.resample(dense[0], dense[1], P.point_spacing * scale, cyc)
                    if snap:
                        pts = [_snap(sampler, p, hover) for p in pts]
                elif self.action == "REVERSE":
                    pts, rad = list(reversed(pts)), list(reversed(rad))
                else:
                    rad = [1.0] * len(rad)
                if len(pts) >= 2:
                    guides.set_spline(o, si, pts, rad)
        pipeline.schedule(target)
        return {"FINISHED"}


def _snap(sampler, p, hover):
    hit = sampler.nearest(p)
    return hit.loc + hit.normal * hover if hit else p


class VINEWRAP_OT_convert_curves(bpy.types.Operator):
    bl_idname = "vine_wrap.convert_curves"
    bl_label = "選択カーブをガイドにする"
    bl_description = "選択中の普通のカーブオブジェクトを、この対象のガイドとして使う"
    bl_options = {"REGISTER", "UNDO"}

    snap: BoolProperty(name="表面に吸着", default=False)

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        st = guides.active_style(P)
        n = 0
        for o in context.selected_objects:
            if o.type != "CURVE" or guides.is_guide(o):
                continue
            mw = o.matrix_world.copy()
            o.vine_guide.target = target
            o.vine_guide.snap = self.snap
            o.vine_guide.style_uid = st.uid
            o.parent = target
            o.matrix_world = mw
            o.show_in_front = True
            n += 1
        self.report({"INFO"}, "%d 本をガイドにしました" % n)
        pipeline.schedule(target)
        return {"FINISHED"}


class VINEWRAP_OT_guide_focus(bpy.types.Operator):
    bl_idname = "vine_wrap.guide_focus"
    bl_label = "選択ガイドを表示"
    bl_description = "ビューを選択中のガイドに合わせる"

    def execute(self, context):
        try:
            bpy.ops.view3d.view_selected()
        except RuntimeError:
            return {"CANCELLED"}
        return {"FINISHED"}


# ======================================================================
# Styles
# ======================================================================
class VINEWRAP_OT_style_add(bpy.types.Operator):
    bl_idname = "vine_wrap.style_add"
    bl_label = "スタイルを追加"
    bl_options = {"REGISTER", "UNDO"}

    duplicate: BoolProperty(default=True, options={"SKIP_SAVE"})

    def execute(self, context):
        P = context.scene.vine_wrap
        guides.ensure_style(P)
        src = guides.active_style(P)
        s = P.styles.add()
        if self.duplicate:
            for k in src.bl_rna.properties.keys():
                if k not in {"rna_type", "uid", "name"}:
                    try:
                        setattr(s, k, getattr(src, k))
                    except (AttributeError, TypeError):
                        pass
        s.name = "スタイル%d" % P.next_uid
        s.uid = P.next_uid
        P.next_uid += 1
        import colorsys
        h = (P.next_uid * 0.137) % 1.0
        s.color = colorsys.hsv_to_rgb(h, 0.6, 1.0)
        P.active_style_index = len(P.styles) - 1
        return {"FINISHED"}


class VINEWRAP_OT_style_remove(bpy.types.Operator):
    bl_idname = "vine_wrap.style_remove"
    bl_label = "スタイルを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_wrap
        if len(P.styles) <= 1:
            self.report({"WARNING"}, "最後のスタイルは削除できません")
            return {"CANCELLED"}
        P.styles.remove(P.active_style_index)
        P.active_style_index = min(P.active_style_index, len(P.styles) - 1)
        pipeline.schedule(P.target)
        return {"FINISHED"}


class VINEWRAP_OT_style_assign(bpy.types.Operator):
    bl_idname = "vine_wrap.style_assign"
    bl_label = "選択ガイドに割り当て"
    bl_description = "アクティブなスタイルを選択中のガイドに割り当てる"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        target = _target(self, context)
        if target is None:
            return {"CANCELLED"}
        P = context.scene.vine_wrap
        st = guides.active_style(P)
        sel = _selected_guides(context, target)
        for o in sel:
            o.vine_guide.style_uid = st.uid
            guides.apply_display(o, P)
        self.report({"INFO"}, "%d 本に「%s」を割り当てました" % (len(sel), st.name))
        pipeline.schedule(target)
        return {"FINISHED"}


classes = (
    VINEWRAP_OT_set_target,
    VINEWRAP_OT_build,
    VINEWRAP_OT_clear,
    VINEWRAP_OT_toggle_rest,
    VINEWRAP_OT_generate,
    VINEWRAP_OT_clear_auto,
    VINEWRAP_OT_clear_paint,
    VINEWRAP_OT_guide_select,
    VINEWRAP_OT_guide_delete,
    VINEWRAP_OT_guide_duplicate,
    VINEWRAP_OT_guide_edit,
    VINEWRAP_OT_convert_curves,
    VINEWRAP_OT_guide_focus,
    VINEWRAP_OT_style_add,
    VINEWRAP_OT_style_remove,
    VINEWRAP_OT_style_assign,
)
