# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty
from mathutils.bvhtree import BVHTree

from . import build, geometry


def active_flower(context):
    obj = context.active_object
    if obj is None:
        return None
    if obj.get("water_flower_water") and obj.parent is not None:
        obj = obj.parent
    if obj.type == 'MESH' and obj.water_flower.is_flower:
        return obj
    return None


class WATERFLOWER_OT_add(bpy.types.Operator):
    """水をためる花を追加"""
    bl_idname = "water_flower.add"
    bl_label = "水をためる花"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name="プリセット",
        items=[
            ('DAFFODIL', "スイセン", ""),
            ('BATH', "風呂", ""),
            ('CHALICE', "聖杯", ""),
            ('PLAY', "あそび", ""),
        ],
        default='DAFFODIL',
    )

    def execute(self, context):
        mesh = bpy.data.meshes.new("WaterFlower")
        obj = bpy.data.objects.new("WaterFlower", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        s = obj.water_flower
        s.lock_update = True
        s.is_flower = True
        s.lock_update = False
        s.preset = self.preset          # 値を入れて作り直す
        if self.preset == 'DAFFODIL' and not obj.data.vertices:
            build.regenerate(obj)
        return {'FINISHED'}


class WATERFLOWER_OT_regenerate(bpy.types.Operator):
    """今の設定で作り直す"""
    bl_idname = "water_flower.regenerate"
    bl_label = "作り直す"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def execute(self, context):
        build.regenerate(active_flower(context))
        return {'FINISHED'}


class WATERFLOWER_OT_bake_animation(bpy.types.Operator):
    """開く / 閉じる動きをシェイプキーのアニメーションとして記録する"""
    bl_idname = "water_flower.bake_animation"
    bl_label = "つぼみアニメーションを作成"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def execute(self, context):
        obj = active_flower(context)
        n = build.bake_animation(obj, context)
        context.scene.frame_set(obj.water_flower.anim_start)
        self.report({'INFO'}, "%d フレームのアニメーションを作成しました" % n)
        return {'FINISHED'}


class WATERFLOWER_OT_clear_animation(bpy.types.Operator):
    """アニメーション (シェイプキー) を消す"""
    bl_idname = "water_flower.clear_animation"
    bl_label = "アニメーションを消す"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = active_flower(context)
        return obj is not None and obj.data.shape_keys is not None

    def execute(self, context):
        build.clear_animation(active_flower(context))
        return {'FINISHED'}


def find_collisions(s, blooms):
    """部品どうしが交差・接触 (厚み以内) する組を探す。"""
    hits = []
    tol = max(s.thickness, 1e-5)
    colors = build.colors_of(s)
    for b in blooms:
        pieces, _, _, _ = geometry.build_flower(s, b, colors, with_water=False)
        trees, boxes = [], []
        for pc in pieces:
            trees.append(BVHTree.FromPolygons(pc.verts, pc.faces))
            xs = [v[0] for v in pc.verts]
            ys = [v[1] for v in pc.verts]
            zs = [v[2] for v in pc.verts]
            boxes.append((min(xs) - tol, max(xs) + tol, min(ys) - tol, max(ys) + tol,
                          min(zs) - tol, max(zs) + tol))
        for i in range(len(pieces)):
            for j in range(i + 1, len(pieces)):
                a, c = boxes[i], boxes[j]
                if a[1] < c[0] or c[1] < a[0] or a[3] < c[2] or c[3] < a[2] or a[5] < c[4] or c[5] < a[4]:
                    continue
                kind = None
                if trees[i].overlap(trees[j]):
                    kind = "交差"
                else:
                    small, big = (i, j) if len(pieces[i].verts) < len(pieces[j].verts) else (j, i)
                    for v in pieces[small].verts:
                        loc, _, _, d = trees[big].find_nearest(v, tol)
                        if loc is not None:
                            kind = "接触"
                            break
                if kind:
                    hits.append((b, pieces[i].name, pieces[j].name, kind))
    return hits


class WATERFLOWER_OT_check(bpy.types.Operator):
    """つぼみ〜満開の途中の形で、部品どうしが交差・接触していないか調べる"""
    bl_idname = "water_flower.check"
    bl_label = "交差チェック"

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def execute(self, context):
        s = active_flower(context).water_flower
        blooms = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
        hits = find_collisions(s, blooms)
        if not hits:
            self.report({'INFO'}, "交差なし (開き具合 0〜1 の 6 段階を確認)")
        else:
            b, a, c, k = hits[0]
            self.report({'WARNING'}, "%d 件: 例) 開き具合 %.1f で %s と %s が%s。"
                        "すき間・重なりのすき間を大きくするか、ねじれ・うねりを小さくしてください"
                        % (len(hits), b, a, c, k))
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(WATERFLOWER_OT_add.bl_idname, icon='OUTLINER_OB_MESH')


classes = (
    WATERFLOWER_OT_add,
    WATERFLOWER_OT_regenerate,
    WATERFLOWER_OT_bake_animation,
    WATERFLOWER_OT_clear_animation,
    WATERFLOWER_OT_check,
)
