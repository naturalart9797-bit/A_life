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
    bl_label = "起点"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        layout.prop(P, "mode", expand=True)
        route_mode = P.mode == "ROUTE"
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("vine_grow.place_origin", text="経由点を置く" if route_mode else "起点を置く",
                     icon="RESTRICT_SELECT_OFF")
        row.operator("vine_grow.origin_at_cursor", text="", icon="PIVOT_CURSOR")
        layout.prop(P, "default_width" if route_mode else "default_reach")
        origins = pipeline.origin_objects(P.target)
        if not origins:
            layout.label(text="対象の上を順番にクリックして置きます" if route_mode else "対象の上をクリックして起点を置きます",
                         icon="INFO")
        for k, o in enumerate(origins):
            row = layout.row(align=True)
            sel = o.name in context.view_layer.objects and o.select_get()
            row.operator("vine_grow.origin_select", text="%d. %s" % (k + 1, o.name), depress=sel).name = o.name
            if o.vine_grow_reach > 0.0:
                row.prop(o, "vine_grow_reach", text="幅" if route_mode else "範囲")
            else:
                row.prop(o, "empty_display_size", text="幅" if route_mode else "範囲")
            if route_mode:
                up = row.operator("vine_grow.origin_move", text="", icon="TRIA_UP")
                up.name, up.delta = o.name, -1
                dn = row.operator("vine_grow.origin_move", text="", icon="TRIA_DOWN")
                dn.name, dn.delta = o.name, 1
            row.operator("vine_grow.origin_remove", text="", icon="X").name = o.name
        if route_mode:
            layout.label(text="1→2→3… の順につるが通過（球の大きさ＝束の幅）", icon="INFO")
        else:
            layout.label(text="範囲 = 球の大きさ（体に沿った距離で広がる）", icon="INFO")


class VINEGROW_PT_network(_Base, bpy.types.Panel):
    bl_label = "つるの広がり"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_grow.target is not None

    def draw(self, context):
        P = context.scene.vine_grow
        layout = self.layout
        if P.mode == "ROUTE":
            col = layout.column(align=True)
            for name in ("strand_count", "twist", "wrap_threshold", "strand_sync"):
                col.prop(P, name)
            col.prop(P, "counter_twist")
            _cols(layout, P, ("spread", "cling", "clearance"),
                  ("meander", "meander_length", "wander", "step"),
                  ("shoot_density", "shoot_length"),
                  ("aerial_count", "aerial_length", "aerial_lift"),
                  ("tendril_chance", "tendril_length"), ("seed",))
        else:
            _cols(layout, P, ("attractor_spacing", "spread", "cling", "clearance"),
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
        _cols(self.layout, P, ("r_min", "r_max") + (("pipe_exponent",) if P.mode != "ROUTE" else ()) + ("ring_res",),
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
