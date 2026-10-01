import random
import time
import traceback

import bpy
from bpy.props import StringProperty

from . import binding
from .growth import finalize_body_paths, grow_body_vines
from .meshgen import MeshBuilder, add_leaves, add_tube, assign_vertex_groups, build_mesh
from .sampler import BodySampler, SubsetView, smoothstep
from .skirt import build_skirt


def _get_body(context, P):
    body = P.body
    if body is None:
        obj = context.active_object
        if obj and obj.type == "MESH" and not obj.get("vine_dress_source"):
            body = obj
    return body


def _remove_generated(body_name):
    for obj in [o for o in bpy.data.objects if o.get("vine_dress_source") == body_name]:
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
        return w * (0.2 + 0.8 * (t or 0.0))
    return w * 0.3


class VINEDRESS_OT_generate(bpy.types.Operator):
    bl_idname = "vine_dress.generate"
    bl_label = "つるの衣装を生成"
    bl_description = "人物メッシュにつる植物を纏わせ、水中部分をスカート状に広げる"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        P = context.scene.vine_dress
        body = _get_body(context, P)
        if body is None:
            self.report({"ERROR"}, "人物メッシュを指定してください")
            return {"CANCELLED"}
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        t0 = time.time()
        if P.replace_existing:
            _remove_generated(body.name)

        mode = P.bind_mode
        if mode == "ARMATURE" and not binding.body_armatures(body):
            self.report({"WARNING"}, "アーマチュアが見つからないためサーフェス変形で追従します")
            mode = "SURFACE"

        try:
            with binding.rest_pose(context, body, P.rest_pose):
                obj, stats = self._build(context, P, body, mode)
        except Exception as exc:  # noqa: BLE001 - surface the reason in the UI
            traceback.print_exc()
            self.report({"ERROR"}, "生成に失敗しました: %s" % exc)
            return {"CANCELLED"}

        if obj is None:
            self.report({"ERROR"}, "つるを生成できませんでした（範囲・マスクを確認してください）")
            return {"CANCELLED"}

        msg = "生成完了: つる %d 本 / 葉 %d 枚 / 頂点 %d (%.1f秒)" % (
            stats["paths"], stats["leaves"], stats["verts"], time.time() - t0)
        if stats.get("bind_failed"):
            self.report({"WARNING"}, msg + " ※サーフェス変形のバインドに失敗")
        else:
            self.report({"INFO"}, msg)
        return {"FINISHED"}

    # ------------------------------------------------------------------
    def _build(self, context, P, body, mode):
        bone_names = binding.bone_names_of(body) if mode == "ARMATURE" else None
        sampler = BodySampler(context, body, P.mask_group, bone_names)
        H = sampler.height
        zmin = sampler.zmin
        scale = H / 1.7 if P.auto_scale else 1.0
        rng = random.Random(P.seed)

        if P.water_object:
            water_z = P.water_object.matrix_world.translation.z
        else:
            water_z = zmin + P.water_level * H
        skirt_z = water_z if P.skirt_at_water else zmin + P.skirt_top * H
        skirt_z = min(skirt_z, sampler.zmax)
        z_hi = zmin + P.cover_top * H
        z_lo = skirt_z - 0.01 * H if P.use_skirt else zmin - H

        paths = []
        if z_hi > z_lo:
            body_paths = grow_body_vines(sampler, P, rng, scale, z_lo, z_hi)
            finalize_body_paths(body_paths, sampler, P, scale)
            for p in body_paths:
                p.weights = [sampler.weights_at(h) for h in p.hits]
            paths += body_paths

        if P.use_skirt:
            view = sampler
            if P.skirt_body_group:
                sub = SubsetView(sampler, body, P.skirt_body_group)
                if sub.valid:
                    view = sub
                else:
                    self.report({"WARNING"}, "スカート追従グループが空のため全身を使用します")
            z_bot = zmin + P.skirt_bottom * H
            paths += build_skirt(sampler, view, P, rng, scale, skirt_z, z_bot)

        if not paths:
            return None, None

        for p in paths:
            ts = p.tparams or [None] * len(p.points)
            p.sway = [_sway_weight(pt.z, water_z, H, p.kind, t) if P.use_sway else 0.0
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

        stats = {"paths": len(paths), "leaves": leaves, "verts": len(b.verts)}
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


class VINEDRESS_OT_clear(bpy.types.Operator):
    bl_idname = "vine_dress.clear"
    bl_label = "生成したつるを削除"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        body = _get_body(context, context.scene.vine_dress)
        if body is None:
            return {"CANCELLED"}
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
    VINEDRESS_OT_generate,
    VINEDRESS_OT_clear,
    VINEDRESS_OT_use_active,
    VINEDRESS_OT_height_from_cursor,
)
