import bpy


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Dress"


class VINEDRESS_PT_main(_Base, bpy.types.Panel):
    bl_label = "つる植物ドレス"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "body")
        row.operator("vine_dress.use_active", text="", icon="EYEDROPPER")
        if P.body:
            layout.prop_search(P, "mask_group", P.body, "vertex_groups")
        col = layout.column(align=True)
        col.prop(P, "seed")
        col.prop(P, "auto_scale")
        col.prop(P, "rest_pose")
        col.prop(P, "replace_existing")
        layout.separator()
        row = layout.row()
        row.scale_y = 1.6
        row.operator("vine_dress.generate", icon="OUTLINER_OB_CURVES")
        layout.operator("vine_dress.clear", icon="TRASH")


class VINEDRESS_PT_heights(_Base, bpy.types.Panel):
    bl_label = "高さ・水面"
    bl_parent_id = "VINEDRESS_PT_main"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout

        def with_cursor(prop):
            row = layout.row(align=True)
            row.prop(P, prop)
            row.operator("vine_dress.height_from_cursor", text="", icon="PIVOT_CURSOR").prop = prop

        with_cursor("cover_top")
        layout.prop(P, "water_object")
        if not P.water_object:
            with_cursor("water_level")
        layout.prop(P, "skirt_at_water")
        if not P.skirt_at_water:
            with_cursor("skirt_top")
        with_cursor("skirt_bottom")


class VINEDRESS_PT_body(_Base, bpy.types.Panel):
    bl_label = "上半身のつる"
    bl_parent_id = "VINEDRESS_PT_main"

    def draw(self, context):
        P = context.scene.vine_dress
        col = self.layout.column(align=True)
        for name in ("vine_count", "coverage_passes", "spacing", "step_length", "max_steps"):
            col.prop(P, name)
        col = self.layout.column(align=True)
        for name in ("wrap", "spiral_bias", "vertical_bias", "persistence", "noise"):
            col.prop(P, name)
        col = self.layout.column(align=True)
        for name in ("branch_chance", "max_depth", "branch_radius"):
            col.prop(P, name)
        col = self.layout.column(align=True)
        for name in ("radius", "taper", "surface_offset", "ring_res"):
            col.prop(P, name)


class VINEDRESS_PT_skirt(_Base, bpy.types.Panel):
    bl_label = "水中スカート"
    bl_parent_id = "VINEDRESS_PT_main"

    def draw_header(self, context):
        self.layout.prop(context.scene.vine_dress, "use_skirt", text="")

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        layout.active = P.use_skirt
        col = layout.column(align=True)
        for name in ("skirt_vines", "skirt_weave", "skirt_rings", "skirt_radius"):
            col.prop(P, name)
        col = layout.column(align=True)
        for name in ("flare", "flare_power", "twist", "ruffle", "ruffle_freq", "skirt_wave",
                     "skirt_ragged"):
            col.prop(P, name)
        col = layout.column(align=True)
        col.prop(P, "skirt_follow_body")
        if P.body:
            col.prop_search(P, "skirt_body_group", P.body, "vertex_groups")
        col.prop(P, "skirt_stiffness")


class VINEDRESS_PT_leaves(_Base, bpy.types.Panel):
    bl_label = "葉"
    bl_parent_id = "VINEDRESS_PT_main"

    def draw_header(self, context):
        self.layout.prop(context.scene.vine_dress, "use_leaves", text="")

    def draw(self, context):
        P = context.scene.vine_dress
        col = self.layout.column(align=True)
        col.active = P.use_leaves
        for name in ("leaf_density", "leaf_size", "leaf_size_var", "leaf_width", "leaf_tilt",
                     "leaf_curl"):
            col.prop(P, name)


class VINEDRESS_PT_anim(_Base, bpy.types.Panel):
    bl_label = "アニメーション追従"
    bl_parent_id = "VINEDRESS_PT_main"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        layout.prop(P, "bind_mode", expand=True)
        layout.prop(P, "use_sway")
        col = layout.column(align=True)
        col.active = P.use_sway
        for name in ("sway_strength", "sway_scale", "sway_speed"):
            col.prop(P, name)


classes = (
    VINEDRESS_PT_main,
    VINEDRESS_PT_heights,
    VINEDRESS_PT_body,
    VINEDRESS_PT_skirt,
    VINEDRESS_PT_leaves,
    VINEDRESS_PT_anim,
)
