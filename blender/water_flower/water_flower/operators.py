# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty, IntProperty

from . import materials


class WATERFLOWER_OT_grow_prototype(bpy.types.Operator):
    """成長シミュレーション (試作): 外花被片と副花冠を、脈と二層の膜の成長から作る。
    数十秒〜数分かかり、その間 Blender は止まって見えます"""
    bl_idname = "water_flower.grow_prototype"
    bl_label = "スイセンを成長させる (試作)"
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
            at = me.attributes.new(materials.COLOR_ATTR, 'FLOAT_COLOR', 'POINT')
            cols = []
            for i in range(ns1):
                c = lerp(c0, c1, i / (ns1 - 1))
                cols += [c[0], c[1], c[2], 1.0] * nv1
            at.data.foreach_set("color", cols)
            mat = bpy.data.materials.new("WF_Grown_" + name)
            materials.build_material(mat, kind)
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
        self.report({'INFO'}, "成長シミュレーションで作りました (付け根はまだ花筒に付いていません)")
        return {'FINISHED'}


def menu_func(self, context):
    self.layout.operator(WATERFLOWER_OT_grow_prototype.bl_idname, icon='EXPERIMENTAL')


classes = (
    WATERFLOWER_OT_grow_prototype,
)
