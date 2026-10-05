# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty, IntProperty
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


def should_check(ka, kb):
    """付け根で接して付いている組 (花びら・がくと子房、しべと器) は調べない。"""
    pair = {ka, kb}
    if 'STEM' in pair:
        return False
    if 'CORONA' in pair and pair & {'STAMEN', 'PISTIL'}:
        return False
    return True


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
                if not should_check(pieces[i].kind, pieces[j].kind):
                    continue
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


class WATERFLOWER_OT_grow_prototype(bpy.types.Operator):
    """成長シミュレーション (試作): 外花被片と副花冠を、脈と二層の膜の成長から作る。
    数十秒〜数分かかり、その間 Blender は止まって見えます"""
    bl_idname = "water_flower.grow_prototype"
    bl_label = "成長で作る (試作)"
    bl_options = {'REGISTER', 'UNDO'}

    quality: EnumProperty(
        name="細かさ",
        items=[
            ('DRAFT', "下書き (速い)", "約 1 分"),
            ('FINE', "細かい (遅い)", "約 3〜5 分"),
        ],
        default='DRAFT',
    )
    tepals: IntProperty(name="外花被片の枚数", default=3, min=0, max=6)

    def execute(self, context):
        import math
        from . import growth
        if self.quality == 'FINE':
            t_lv, t_it = [(12, 5), (24, 10), (48, 20), (96, 40)], (20000, 8000, 3000, 1500)
            c_lv, c_it = [(24, 112), (48, 224)], (12000, 3000)
        else:
            t_lv, t_it = [(12, 5), (24, 10), (48, 20)], (12000, 5000, 1500)
            c_lv, c_it = [(16, 80), (32, 160)], (8000, 2000)
        cor, _ = growth.make_corona(levels=c_lv, iters=c_it, rim_width=0.15, rim_growth=0.6,
                                    thickness_rim=0.4, flare=1.0, height=0.5, radius=0.14,
                                    vein_relief=0.02)
        tep = None
        if self.tepals:
            tep, _ = growth.make_tepal(levels=t_lv, iters=t_it, margin_growth=0.16,
                                       vein_relief=0.03, keel=8)
        root = bpy.data.objects.new("GrownDaffodil", None)
        context.collection.objects.link(root)
        root.location = context.scene.cursor.location

        def lerp(a, b, t):
            return tuple(x + (y - x) * t for x, y in zip(a, b))

        def make(name, X, periodic, kind, c0, c1):
            ns1, nv1, _ = X.shape
            me = bpy.data.meshes.new(name)
            me.from_pydata(X.reshape(-1, 3).tolist(), [], growth.grid_faces(ns1, nv1, periodic))
            me.update()
            me.polygons.foreach_set("use_smooth", [True] * len(me.polygons))
            uv = me.uv_layers.new(name="UVMap")
            lv = [0] * len(me.loops)
            me.loops.foreach_get("vertex_index", lv)
            us = []
            for vi in lv:
                i, j = divmod(vi, nv1)
                us += [j / (nv1 if periodic else nv1 - 1), i / (ns1 - 1)]
            uv.data.foreach_set("uv", us)
            at = me.attributes.new(build.COLOR_ATTR, 'FLOAT_COLOR', 'POINT')
            cols = []
            for i in range(ns1):
                c = lerp(c0, c1, i / (ns1 - 1))
                cols += [c[0], c[1], c[2], 1.0] * nv1
            at.data.foreach_set("color", cols)
            mat = bpy.data.materials.new("WF_Grown_" + name)
            build.build_material(mat, kind)
            mat.node_tree.nodes["wf_vein_strength"].outputs[0].default_value = 0.5
            mat.node_tree.nodes["wf_translucency"].outputs[0].default_value = 0.4
            me.materials.append(mat)
            ob = bpy.data.objects.new(name, me)
            context.collection.objects.link(ob)
            ob.parent = root
            return ob

        make("Corona", cor, True, 'CORONA', (1.0, 0.38, 0.02), (1.0, 0.55, 0.08))
        for k in range(self.tepals):
            ob = make("Tepal_%d" % k, tep, False, 'PETAL', (1.0, 0.72, 0.12), (1.0, 0.88, 0.35))
            az = 2 * math.pi * k / self.tepals - 0.6
            ob.rotation_euler = (0.0, -math.radians(20.0), az)
            ob.location = (0.15 * math.cos(az), 0.15 * math.sin(az), 0.02)
        for o in context.selected_objects:
            o.select_set(False)
        root.select_set(True)
        context.view_layer.objects.active = root
        self.report({'INFO'}, "成長シミュレーションの試作を作りました (付け根はまだ花筒に付いていません)")
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(WATERFLOWER_OT_add.bl_idname, icon='OUTLINER_OB_MESH')


classes = (
    WATERFLOWER_OT_add,
    WATERFLOWER_OT_regenerate,
    WATERFLOWER_OT_bake_animation,
    WATERFLOWER_OT_clear_animation,
    WATERFLOWER_OT_check,
    WATERFLOWER_OT_grow_prototype,
)
