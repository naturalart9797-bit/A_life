import bpy

from . import pipeline


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Slime Net"


def _wrap(text, n):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def _cols(layout, data, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(data, name)


class SLIMENET_PT_main(_Base, bpy.types.Panel):
    bl_label = "粘菌ネットワーク"

    def draw(self, context):
        P = context.scene.slime_net
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "target")
        row.operator("slime_net.set_target", text="", icon="EYEDROPPER")
        if P.target is None:
            layout.label(text="ネットワークを広げるメッシュを選んでください", icon="INFO")
            return
        row = layout.row()
        row.scale_y = 1.5
        row.operator("slime_net.generate", icon="PLAY")
        if P.last_message:
            box = layout.box()
            box.alert = not P.last_ok
            for line in _wrap(P.last_message, 22):
                box.label(text=line, icon="NONE")
        row = layout.row(align=True)
        row.operator("slime_net.toggle_rest", icon="ARMATURE_DATA")
        row.operator("slime_net.clear", text="", icon="TRASH")
        nets = pipeline.network_objects(P.target)
        if nets:
            layout.prop(P, "growth", slider=True)
            node = pipeline.growth_node(nets[0])
            if node is not None:
                layout.prop(node.outputs[0], "default_value", text="成長（キーフレーム用）", slider=True)


class SLIMENET_PT_origins(_Base, bpy.types.Panel):
    bl_label = "起点と範囲"

    @classmethod
    def poll(cls, context):
        return context.scene.slime_net.target is not None

    def draw(self, context):
        P = context.scene.slime_net
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("slime_net.place_origin", icon="RESTRICT_SELECT_OFF")
        row.operator("slime_net.origin_at_cursor", text="", icon="PIVOT_CURSOR")
        layout.prop(P, "default_reach")
        origins = pipeline.origin_objects(P.target)
        if not origins:
            layout.label(text="対象の上をクリックして起点を置きます", icon="INFO")
        for o in origins:
            row = layout.row(align=True)
            sel = o.name in context.view_layer.objects and o.select_get()
            row.operator("slime_net.origin_select", text=o.name, depress=sel).name = o.name
            if o.slime_reach > 0.0:
                row.prop(o, "slime_reach", text="範囲")
            else:
                row.prop(o, "empty_display_size", text="範囲")
            row.operator("slime_net.origin_remove", text="", icon="X").name = o.name
        layout.label(text="範囲 = 球の大きさ（表面に沿った距離で広がる）", icon="INFO")


class SLIMENET_PT_network(_Base, bpy.types.Panel):
    bl_label = "ネットワークの形"

    @classmethod
    def poll(cls, context):
        return context.scene.slime_net.target is not None

    def draw(self, context):
        P = context.scene.slime_net
        _cols(self.layout, P, ("spacing", "food_count", "front_ratio"),
              ("loopiness", "keep_decades", "origin_bias"), ("iterations", "seed"))


class SLIMENET_PT_look(_Base, bpy.types.Panel):
    bl_label = "太さ・見た目"

    @classmethod
    def poll(cls, context):
        return context.scene.slime_net.target is not None

    def draw(self, context):
        P = context.scene.slime_net
        _cols(self.layout, P, ("r_min", "r_max", "surface_offset", "ring_res"),
              ("color_thin", "color_thick", "roughness", "subsurface"))


class SLIMENET_PT_settings(_Base, bpy.types.Panel):
    bl_label = "追従・設定"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.slime_net.target is not None

    def draw(self, context):
        P = context.scene.slime_net
        self.layout.prop(P, "bind_mode")
        _cols(self.layout, P, ("auto_scale", "rest_pose"))


classes = (SLIMENET_PT_main, SLIMENET_PT_origins, SLIMENET_PT_network, SLIMENET_PT_look, SLIMENET_PT_settings)
