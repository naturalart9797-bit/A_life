import bpy

from . import pipeline


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Grow"


def _wrap(text, n):
    return [text[i:i + n] for i in range(0, len(text), n)] or [""]


def _cols(layout, data, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(data, name)


class VINEGROW_PT_main(_Base, bpy.types.Panel):
    bl_label = "つる植物"

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "target")
        row.operator("vine_grow.set_target", text="", icon="EYEDROPPER")
        if P.target is None:
            layout.label(text="つるを生やすメッシュを選んでください", icon="INFO")
            return
        row = layout.row()
        row.scale_y = 1.5
        row.operator("vine_grow.generate", icon="PLAY")
        if P.last_message:
            box = layout.box()
            box.alert = not P.last_ok
            for line in _wrap(P.last_message, 22):
                box.label(text=line, icon="NONE")
        row = layout.row(align=True)
        row.operator("vine_grow.toggle_rest", icon="ARMATURE_DATA")
        row.operator("vine_grow.clear", text="", icon="TRASH")
        nets = pipeline.network_objects(P.target)
        if nets:
            layout.prop(P, "growth", slider=True)
            node = pipeline.growth_node(nets[0])
            if node is not None:
                layout.prop(node.outputs[0], "default_value", text="成長（キーフレーム用）", slider=True)


class VINEGROW_PT_origins(_Base, bpy.types.Panel):
    bl_label = "起点と範囲"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("vine_grow.place_origin", icon="RESTRICT_SELECT_OFF")
        row.operator("vine_grow.origin_at_cursor", text="", icon="PIVOT_CURSOR")
        layout.prop(P, "default_reach")
        origins = pipeline.origin_objects(P.target)
        if not origins:
            layout.label(text="対象の上をクリックして起点を置きます", icon="INFO")
        for o in origins:
            row = layout.row(align=True)
            sel = o.name in context.view_layer.objects and o.select_get()
            row.operator("vine_grow.origin_select", text=o.name, depress=sel).name = o.name
            if o.vine_grow_reach > 0.0:
                row.prop(o, "vine_grow_reach", text="範囲")
            else:
                row.prop(o, "empty_display_size", text="範囲")
            row.operator("vine_grow.origin_remove", text="", icon="X").name = o.name
        layout.label(text="範囲 = 球の大きさ（表面に沿った距離で広がる）", icon="INFO")


class VINEGROW_PT_network(_Base, bpy.types.Panel):
    bl_label = "つるの広がり"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        _cols(self.layout, P, ("attractor_spacing", "spread", "cling", "clearance"),
              ("influence", "inertia", "meander", "meander_length", "wander", "min_twig", "step"),
              ("aerial_count", "aerial_length", "aerial_lift"),
              ("tendril_chance", "tendril_length"),
              ("max_iterations", "max_nodes", "seed"))


class VINEGROW_PT_look(_Base, bpy.types.Panel):
    bl_label = "太さ・見た目"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        _cols(self.layout, P, ("r_min", "r_max", "pipe_exponent", "ring_res"),
              ("color_thin", "color_thick", "roughness", "subsurface", "bump"))


class VINEGROW_PT_settings(_Base, bpy.types.Panel):
    bl_label = "追従・設定"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        self.layout.prop(P, "bind_mode")
        _cols(self.layout, P, ("auto_scale", "rest_pose"))


classes = (VINEGROW_PT_main, VINEGROW_PT_origins, VINEGROW_PT_network, VINEGROW_PT_look, VINEGROW_PT_settings)
