import bpy

from . import guides


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Dress"


def _cols(layout, P, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(P, name)


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
        layout.operator("vine_dress.toggle_rest", icon="ARMATURE_DATA")
        layout.separator()
        layout.operator("vine_dress.generate", icon="SHADERFX")


class VINEDRESS_PT_heights(_Base, bpy.types.Panel):
    bl_label = "高さ・水面"
    bl_parent_id = "VINEDRESS_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

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


# ----------------------------------------------------------------------
# Stage 1
# ----------------------------------------------------------------------
class VINEDRESS_PT_guides(_Base, bpy.types.Panel):
    bl_label = "① ガイドカーブ"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        body = P.body
        if body is not None:
            objs = guides.guide_objects(body)
            n = sum(len(o.data.splines) for o in objs)
            layout.label(text="ガイド: %d オブジェクト / %d 本" % (len(objs), n), icon="CURVE_DATA")

        row = layout.row(align=True)
        row.prop(P, "gen_body_guides", toggle=True)
        row.prop(P, "gen_skirt_guides", toggle=True)
        layout.prop(P, "replace_auto_guides")
        row = layout.row()
        row.scale_y = 1.4
        row.operator("vine_dress.generate_guides", icon="OUTLINER_OB_CURVE")

        layout.label(text="手で描く / 編集:")
        row = layout.row(align=True)
        row.operator("vine_dress.draw_guide", text="体表面に描く", icon="GREASEPENCIL").kind = guides.KIND_BODY
        row.operator("vine_dress.draw_guide", text="空間に描く", icon="CURVE_PATH").kind = guides.KIND_SKIRT
        layout.operator("vine_dress.snap_guides", icon="SNAP_ON")
        row = layout.row(align=True)
        row.operator("vine_dress.clear_guides", text="自動分を削除", icon="TRASH").auto_only = True
        row.operator("vine_dress.clear_guides", text="全て削除", icon="TRASH").auto_only = False


class VINEDRESS_PT_guide_active(_Base, bpy.types.Panel):
    bl_label = "選択中のガイド"
    bl_parent_id = "VINEDRESS_PT_guides"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == "CURVE" and obj.vine_guide.body is not None

    def draw(self, context):
        gs = context.active_object.vine_guide
        layout = self.layout
        layout.prop(gs, "enabled")
        layout.prop(gs, "kind", expand=True)
        col = layout.column(align=True)
        col.prop(gs, "radius")
        col.prop(gs, "leaves")
        col.prop(gs, "strands")
        layout.label(text="制御点の半径(Alt+S)で部分的な太さを調整", icon="INFO")


class VINEDRESS_PT_auto_body(_Base, bpy.types.Panel):
    bl_label = "自動生成: 上半身"
    bl_parent_id = "VINEDRESS_PT_guides"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_dress
        _cols(self.layout, P,
              ("vine_count", "coverage_passes", "spacing", "step_length", "max_steps"),
              ("wrap", "spiral_bias", "vertical_bias", "persistence", "noise"),
              ("branch_chance", "max_depth", "branch_radius", "taper"),
              ("guide_point_spacing",))


class VINEDRESS_PT_auto_skirt(_Base, bpy.types.Panel):
    bl_label = "自動生成: 水中スカート"
    bl_parent_id = "VINEDRESS_PT_guides"
    bl_options = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.prop(context.scene.vine_dress, "use_skirt", text="")

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        layout.active = P.use_skirt
        _cols(layout, P,
              ("skirt_vines", "skirt_weave", "skirt_rings"),
              ("flare", "flare_power", "twist", "ruffle", "ruffle_freq", "skirt_wave", "skirt_ragged"),
              ("skirt_follow_body",))
        if P.body:
            layout.prop_search(P, "skirt_body_group", P.body, "vertex_groups")


# ----------------------------------------------------------------------
# Stage 2
# ----------------------------------------------------------------------
class VINEDRESS_PT_build(_Base, bpy.types.Panel):
    bl_label = "② つる生成"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        layout.prop(P, "replace_existing")
        layout.prop(P, "snap_on_build")
        row = layout.row()
        row.scale_y = 1.6
        row.operator("vine_dress.build", icon="OUTLINER_OB_CURVES")
        layout.operator("vine_dress.clear", icon="TRASH")


class VINEDRESS_PT_build_stem(_Base, bpy.types.Panel):
    bl_label = "つる・巻きひげ"
    bl_parent_id = "VINEDRESS_PT_build"

    def draw(self, context):
        P = context.scene.vine_dress
        _cols(self.layout, P,
              ("radius", "skirt_radius", "surface_offset", "ring_res"),
              ("strands", "strand_spread", "strand_twist", "strand_radius"),
              ("tendril_density", "tendril_size"))


class VINEDRESS_PT_leaves(_Base, bpy.types.Panel):
    bl_label = "葉"
    bl_parent_id = "VINEDRESS_PT_build"

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
    bl_parent_id = "VINEDRESS_PT_build"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        layout.prop(P, "bind_mode", expand=True)
        layout.prop(P, "skirt_stiffness")
        layout.prop(P, "use_sway")
        col = layout.column(align=True)
        col.active = P.use_sway
        for name in ("sway_strength", "sway_scale", "sway_speed"):
            col.prop(P, name)


classes = (
    VINEDRESS_PT_main,
    VINEDRESS_PT_heights,
    VINEDRESS_PT_guides,
    VINEDRESS_PT_guide_active,
    VINEDRESS_PT_auto_body,
    VINEDRESS_PT_auto_skirt,
    VINEDRESS_PT_build,
    VINEDRESS_PT_build_stem,
    VINEDRESS_PT_leaves,
    VINEDRESS_PT_anim,
)
