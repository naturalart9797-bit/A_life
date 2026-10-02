import random
import time
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import binding, guides
from .build import collect, paths_from_guides
from .growth import finalize_body_paths, grow_body_vines
from .meshgen import MeshBuilder, add_leaves, add_tube, assign_vertex_groups, build_mesh
from .sampler import BodySampler, SubsetView, smoothstep
from .skirt import SkirtField, SkirtShape, build_skirt


def _get_body(context, P):
    body = P.body
    if body is None:
        obj = context.active_object
        if obj and obj.type == "MESH" and not obj.get("vine_dress_source"):
            body = obj
    return body


def _vine_objects(body_name):
    return [o for o in bpy.data.objects if o.get("vine_dress_source") == body_name]


def _remove_generated(body_name):
    for obj in _vine_objects(body_name):
        data = obj.data
        tex = []
        for m in getattr(obj, "modifiers", []):
            if m.type == "DISPLACE" and m.texture:
                tex.append(m.texture)
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
        for t in tex:
            if t.users == 0:
                bpy.data.textures.remove(t)


def _sway_weight(z, water_z, H, kind, t):
    depth = water_z - z
    if depth <= 0.0:
        return 0.0
    w = smoothstep(depth / (0.15 * H))
    if kind == "skirt":
        return w * (0.2 + 0.8 * max(0.0, min(1.0, t or 0.0)))
    return w * 0.3


class Levels:
    """Heights in world space derived from the settings and the body size."""

    def __init__(self, P, sampler):
        H = sampler.height
        zmin = sampler.zmin
        self.H = H
        self.scale = H / 1.7 if P.auto_scale else 1.0
        if P.water_object:
            self.water_z = P.water_object.matrix_world.translation.z
        else:
            self.water_z = zmin + P.water_level * H
        skirt_z = self.water_z if P.skirt_at_water else zmin + P.skirt_top * H
        self.skirt_z = min(skirt_z, sampler.zmax)
        self.skirt_bot = zmin + P.skirt_bottom * H
        self.z_hi = zmin + P.cover_top * H
        self.z_lo = self.skirt_z - 0.01 * H if P.use_skirt else zmin - H


def _skirt_view(op, sampler, body, P):
    if P.skirt_body_group:
        sub = SubsetView(sampler, body, P.skirt_body_group)
        if sub.valid:
            return sub
        op.report({"WARNING"}, "スカート追従グループが空のため全身を使用します")
    return sampler


def _guide_hover(P, scale):
    # Guides float just above the skin so they stay visible while editing.
    return (P.surface_offset + P.radius * 2.0) * scale


def _ensure_object_mode(context):
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


# ======================================================================
# Stage 1: guide curves
# ======================================================================
def make_guides(op, context, P, body):
    if P.replace_auto_guides:
        guides.remove_guides(body, auto_only=True)
    count = 0
    with binding.rest_pose(context, body, P.rest_pose):
        sampler = BodySampler(context, body, P.mask_group)
        lv = Levels(P, sampler)
        rng = random.Random(P.seed)
        spacing = P.guide_point_spacing * lv.scale

        if P.gen_body_guides and lv.z_hi > lv.z_lo:
            paths = grow_body_vines(sampler, P, rng, lv.scale, lv.z_lo, lv.z_hi)
            finalize_body_paths(paths, sampler, P)
            hover = _guide_hover(P, lv.scale)
            for p in paths:
                p.points = [pt + n * hover for pt, n in zip(p.points, p.normals)]
            if paths:
                obj = guides.new_guide_object(context, body, guides.KIND_BODY,
                                              body.name + "_Guide_Body", auto=True)
                guides.write_paths(obj, paths, spacing)
                count += len(obj.data.splines)

        if P.gen_skirt_guides and P.use_skirt:
            view = _skirt_view(op, sampler, body, P)
            paths = build_skirt(sampler, view, P, rng, lv.scale, lv.skirt_z, lv.skirt_bot)
            if paths:
                obj = guides.new_guide_object(context, body, guides.KIND_SKIRT,
                                              body.name + "_Guide_Skirt", auto=True)
                guides.write_paths(obj, paths, spacing)
                count += len(obj.data.splines)
    return count


class VINEDRESS_OT_generate_guides(bpy.types.Operator):
    bl_idname = "vine_dress.generate_guides"
    bl_label = "ガイドを自動生成"
    bl_description = "体に吸着したガイドカーブ（上半身）とスカートのガイドカーブを自動で作る。生成後は自由に編集できる"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            self.report({"ERROR"}, "人物メッシュを指定してください")
            return {"CANCELLED"}
        _ensure_object_mode(context)
        try:
            n = make_guides(self, context, P, body)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "ガイド生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        self.report({"INFO"}, "ガイドカーブ %d 本を生成しました" % n)
        return {"FINISHED"}


class VINEDRESS_OT_draw_guide(bpy.types.Operator):
    bl_idname = "vine_dress.draw_guide"
    bl_label = "ガイドを描く"
    bl_description = "新しいガイドカーブを作り、描画ツールで描けるようにする"
    bl_options = {"REGISTER", "UNDO"}

    kind: EnumProperty(items=[
        (guides.KIND_BODY, "体表面", "体の表面に描く（表面に吸着）"),
        (guides.KIND_SKIRT, "スカート/空間", "3Dカーソルの奥行きの平面に描く"),
    ])

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            self.report({"ERROR"}, "人物メッシュを指定してください")
            return {"CANCELLED"}
        _ensure_object_mode(context)
        # Hide generated vines so strokes snap to the skin, not the leaves.
        for o in _vine_objects(body.name):
            if o.name in context.view_layer.objects:
                o.hide_set(True)

        suffix = "_Guide_Draw" if self.kind == guides.KIND_BODY else "_Guide_DrawSkirt"
        obj = guides.new_guide_object(context, body, self.kind, body.name + suffix)
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")

        cps = context.scene.tool_settings.curve_paint_settings
        cps.depth_mode = "SURFACE" if self.kind == guides.KIND_BODY else "CURSOR"
        cps.surface_offset = 0.0
        cps.use_pressure_radius = False
        cps.radius_max = 1.0
        cps.radius_taper_start = 0.0
        cps.radius_taper_end = 0.3
        if context.space_data is not None and context.space_data.type == "VIEW_3D":
            try:
                bpy.ops.wm.tool_set_by_id(name="builtin.draw")
            except Exception:  # noqa: BLE001 - not fatal, user can pick the tool
                pass
        self.report({"INFO"}, "ドラッグしてガイドを描いてください（終わったらTabで抜ける）")
        return {"FINISHED"}


class VINEDRESS_OT_snap_guides(bpy.types.Operator):
    bl_idname = "vine_dress.snap_guides"
    bl_label = "ガイドを体に吸着"
    bl_description = "選択中（未選択なら全て）の『体表面』ガイドの制御点を体の表面に移動する"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            return {"CANCELLED"}
        _ensure_object_mode(context)
        objs = [o for o in context.selected_objects if guides.is_guide_of(o, body)]
        if not objs:
            objs = guides.guide_objects(body)
        objs = [o for o in objs if o.vine_guide.kind == guides.KIND_BODY]
        moved = 0
        with binding.rest_pose(context, body, P.rest_pose):
            sampler = BodySampler(context, body)
            for o in objs:
                moved += guides.snap_object(o, sampler, _guide_hover(P, Levels(P, sampler).scale))
        self.report({"INFO"}, "%d 個の制御点を吸着しました" % moved)
        return {"FINISHED"}


class VINEDRESS_OT_clear_guides(bpy.types.Operator):
    bl_idname = "vine_dress.clear_guides"
    bl_label = "ガイドを削除"
    bl_options = {"REGISTER", "UNDO"}

    auto_only: BoolProperty(name="自動生成分のみ", default=False)

    def execute(self, context):
        body = _get_body(context, context.scene.vine_dress)
        if body is None:
            return {"CANCELLED"}
        _ensure_object_mode(context)
        guides.remove_guides(body, self.auto_only)
        return {"FINISHED"}


class VINEDRESS_OT_toggle_rest(bpy.types.Operator):
    bl_idname = "vine_dress.toggle_rest"
    bl_label = "レストポーズ切替"
    bl_description = "人物のアーマチュアをレストポーズ/ポーズで切り替える（ガイドはレストポーズで編集する）"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _get_body(context, context.scene.vine_dress)
        if body is None:
            return {"CANCELLED"}
        for arm in binding.body_armatures(body):
            arm.data.pose_position = "POSE" if arm.data.pose_position == "REST" else "REST"
        return {"FINISHED"}


# ======================================================================
# Stage 2: vines from guides
# ======================================================================
def build_vines(op, context, P, body):
    if P.replace_existing:
        _remove_generated(body.name)
    mode = P.bind_mode
    if mode == "ARMATURE" and not binding.body_armatures(body):
        op.report({"WARNING"}, "アーマチュアが見つからないためサーフェス変形で追従します")
        mode = "SURFACE"

    with binding.rest_pose(context, body, P.rest_pose):
        bone_names = binding.bone_names_of(body) if mode == "ARMATURE" else None
        sampler = BodySampler(context, body, P.mask_group, bone_names)
        lv = Levels(P, sampler)
        scale = lv.scale
        rng = random.Random(P.seed + 7919)

        splines = collect(body, P, scale)
        if not splines:
            return None, "ガイドカーブがありません。先に「ガイドを自動生成」するか描いてください"

        field = None
        if any(s.kind == guides.KIND_SKIRT for s in splines) and lv.skirt_z - lv.skirt_bot > 1e-4:
            shape = SkirtShape(sampler, _skirt_view(op, sampler, body, P), P, scale,
                               lv.skirt_z, lv.skirt_bot)
            field = SkirtField(shape, P)

        paths = paths_from_guides(splines, sampler, field, P, rng, scale)
        if not paths:
            return None, "つるを生成できませんでした"

        for p in paths:
            ts = p.tparams or [None] * len(p.points)
            p.sway = [_sway_weight(pt.z, lv.water_z, lv.H, p.kind, t) if P.use_sway else 0.0
                      for pt, t in zip(p.points, ts)]

        b = MeshBuilder()
        for p in paths:
            add_tube(b, p, P.ring_res, rng)
        stem_faces = len(b.faces)
        for p in paths:
            add_leaves(b, p, P, rng, scale)
        leaves = (len(b.faces) - stem_faces) // 8

        name = body.name + "_VineDress"
        me = build_mesh(b, name, sampler.to_local)
        obj = bpy.data.objects.new(name, me)
        obj["vine_dress_source"] = body.name
        colls = body.users_collection or (context.scene.collection,)
        colls[0].objects.link(obj)
        binding.attach(obj, body)
        binding.ensure_materials(obj)
        assign_vertex_groups(obj, b, bone_names if mode == "ARMATURE" else set(),
                             binding.SWAY_GROUP if P.use_sway else "")

        stats = {"paths": len(paths), "guides": len(splines), "leaves": leaves,
                 "verts": len(b.verts)}
        if mode == "ARMATURE":
            binding.add_armature(obj, body)
        elif mode == "SURFACE":
            context.view_layer.update()
            mod = binding.add_surface_deform(context, obj, body)
            if not mod.is_bound:
                stats["bind_failed"] = True
        if P.use_sway and P.sway_strength > 0.0:
            binding.add_sway(obj, P, scale)
    return obj, stats


def _report_build(op, stats, t0):
    msg = "つる生成完了: ガイド %d 本 → つる %d 本 / 葉 %d 枚 / 頂点 %d (%.1f秒)" % (
        stats["guides"], stats["paths"], stats["leaves"], stats["verts"], time.time() - t0)
    if stats.get("bind_failed"):
        op.report({"WARNING"}, msg + " ※サーフェス変形のバインドに失敗")
    else:
        op.report({"INFO"}, msg)


class VINEDRESS_OT_build(bpy.types.Operator):
    bl_idname = "vine_dress.build"
    bl_label = "ガイドからつるを生成"
    bl_description = "ガイドカーブに沿ってつる・葉・巻きひげのメッシュを作り、人物のアニメーションに追従させる"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            self.report({"ERROR"}, "人物メッシュを指定してください")
            return {"CANCELLED"}
        _ensure_object_mode(context)
        t0 = time.time()
        try:
            obj, info = build_vines(self, context, P, body)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        if obj is None:
            self.report({"ERROR"}, info)
            return {"CANCELLED"}
        _report_build(self, info, t0)
        return {"FINISHED"}


class VINEDRESS_OT_generate(bpy.types.Operator):
    bl_idname = "vine_dress.generate"
    bl_label = "おまかせ一括生成"
    bl_description = "ガイドの自動生成と、つるの生成をまとめて行う"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            self.report({"ERROR"}, "人物メッシュを指定してください")
            return {"CANCELLED"}
        _ensure_object_mode(context)
        t0 = time.time()
        try:
            make_guides(self, context, P, body)
            obj, info = build_vines(self, context, P, body)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.report({"ERROR"}, "生成に失敗しました: %s" % exc)
            return {"CANCELLED"}
        if obj is None:
            self.report({"ERROR"}, info)
            return {"CANCELLED"}
        _report_build(self, info, t0)
        return {"FINISHED"}


class VINEDRESS_OT_clear(bpy.types.Operator):
    bl_idname = "vine_dress.clear"
    bl_label = "つるを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _get_body(context, context.scene.vine_dress)
        if body is None:
            return {"CANCELLED"}
        _ensure_object_mode(context)
        _remove_generated(body.name)
        return {"FINISHED"}


class VINEDRESS_OT_use_active(bpy.types.Operator):
    bl_idname = "vine_dress.use_active"
    bl_label = "選択中を人物に設定"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != "MESH":
            self.report({"ERROR"}, "メッシュを選択してください")
            return {"CANCELLED"}
        context.scene.vine_dress.body = obj
        return {"FINISHED"}


class VINEDRESS_OT_height_from_cursor(bpy.types.Operator):
    bl_idname = "vine_dress.height_from_cursor"
    bl_label = "3Dカーソルの高さを使う"
    bl_description = "3Dカーソルの高さを身長に対する割合に変換して設定する"
    bl_options = {"REGISTER", "UNDO"}

    prop: StringProperty()

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            return {"CANCELLED"}
        mw = body.matrix_world
        zs = [(mw @ v.co).z for v in body.data.vertices]
        if not zs:
            return {"CANCELLED"}
        zmin, zmax = min(zs), max(zs)
        frac = (context.scene.cursor.location.z - zmin) / max(zmax - zmin, 1e-6)
        setattr(P, self.prop, frac)
        return {"FINISHED"}


classes = (
    VINEDRESS_OT_generate_guides,
    VINEDRESS_OT_draw_guide,
    VINEDRESS_OT_snap_guides,
    VINEDRESS_OT_clear_guides,
    VINEDRESS_OT_toggle_rest,
    VINEDRESS_OT_build,
    VINEDRESS_OT_generate,
    VINEDRESS_OT_clear,
    VINEDRESS_OT_use_active,
    VINEDRESS_OT_height_from_cursor,
)
