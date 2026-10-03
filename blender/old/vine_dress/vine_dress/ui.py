import bpy

from . import groom_tree, guides
from .tools import TOOLS


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Dress"


def _cols(layout, P, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(P, name)


class VINEDRESS_UL_groups(bpy.types.UIList):
    """Guide groups of the current body (filtered view on bpy.data.objects)."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        gs = item.vine_guide
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.ui_units_x = 1.0
        sub.prop(gs, "color", text="")
        kind_icon = "MOD_SHRINKWRAP" if gs.kind == guides.KIND_BODY else "MOD_CLOTH"
        row.prop(item, "name", text="", emboss=False, icon=kind_icon)
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.label(text=str(len(item.data.splines)))
        sub.prop(gs, "visible", text="", emboss=False, icon="HIDE_OFF" if gs.visible else "HIDE_ON")
        sub.prop(gs, "locked", text="", emboss=False, icon="LOCKED" if gs.locked else "UNLOCKED")

    def filter_items(self, context, data, propname):
        body = context.scene.vine_dress.body
        objs = getattr(data, propname)
        flt = [self.bitflag_filter_item if guides.is_guide_of(o, body) else 0 for o in objs]
        return flt, []


class VINEDRESS_MT_group_add(bpy.types.Menu):
    bl_idname = "VINEDRESS_MT_group_add"
    bl_label = "グループを追加"

    def draw(self, context):
        for kind, label, _d, icon, _n in guides.KIND_ITEMS:
            self.layout.operator("vine_dress.group_add", text=label, icon=icon).kind = kind


# ----------------------------------------------------------------------
class VINEDRESS_PT_main(_Base, bpy.types.Panel):
    bl_label = "つる植物ドレス"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "body")
        row.operator("vine_dress.use_active", text="", icon="EYEDROPPER")
        row = layout.row(align=True)
        row.prop(P, "groom_tree", text="ツリー")
        row.operator("vine_dress.open_tree", text="", icon="NODETREE")
        layout.operator("vine_dress.toggle_rest", icon="ARMATURE_DATA")
        row = layout.row()
        row.scale_y = 1.3
        row.operator("vine_dress.generate", icon="SHADERFX")


class VINEDRESS_PT_heights(_Base, bpy.types.Panel):
    bl_label = "人物・高さ・水面"
    bl_parent_id = "VINEDRESS_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        col = layout.column(align=True)
        col.prop(P, "seed")
        col.prop(P, "auto_scale")
        col.prop(P, "rest_pose")
        if P.body:
            layout.prop_search(P, "mask_group", P.body, "vertex_groups")

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
class VINEDRESS_PT_groups(_Base, bpy.types.Panel):
    bl_label = "ガイドグループ"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        if P.body is None:
            layout.label(text="人物メッシュを設定してください", icon="ERROR")
            return
        row = layout.row()
        row.template_list("VINEDRESS_UL_groups", "", bpy.data, "objects", P, "active_group_index", rows=4)
        col = row.column(align=True)
        col.menu("VINEDRESS_MT_group_add", text="", icon="ADD")
        col.operator("vine_dress.group_remove", text="", icon="REMOVE")

        objs = bpy.data.objects
        act = objs[P.active_group_index] if 0 <= P.active_group_index < len(objs) else None
        if act is not None and guides.is_guide_of(act, P.body):
            box = layout.box()
            box.prop(act.vine_guide, "kind", expand=True)
            tree = P.groom_tree
            if tree is not None and not groom_tree.group_in_tree(tree, act):
                box.label(text="ツリーに未接続", icon="ERROR")
                box.operator("vine_dress.tree_sync_groups", icon="ADD")

        layout.label(text="選択したガイド:")
        row = layout.row(align=True)
        row.operator("vine_dress.select_guides", text="全選択").action = "SELECT"
        row.operator("vine_dress.select_guides", text="解除").action = "DESELECT"
        row.operator("vine_dress.select_guides", text="反転").action = "INVERT"
        row = layout.row(align=True)
        row.operator("vine_dress.move_selected", text="アクティブへ移動", icon="FORWARD")
        row.operator("vine_dress.move_selected", text="新グループへ", icon="ADD").new_group = True
        row = layout.row(align=True)
        row.operator("vine_dress.snap_guides", icon="SNAP_ON")
        row.operator("vine_dress.delete_selected", text="削除", icon="TRASH")


class VINEDRESS_PT_tools(_Base, bpy.types.Panel):
    bl_label = "グルームツール"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        active = ""
        try:
            active = context.workspace.tools.from_space_view3d_mode(context.mode, create=False).idname
        except Exception:  # noqa: BLE001
            pass
        grid = layout.grid_flow(columns=2, align=True, even_columns=True)
        for cls in TOOLS:
            op = grid.operator("wm.tool_set_by_id", text=cls.bl_label, depress=(active == cls.bl_idname))
            op.name = cls.bl_idname
        layout.operator("wm.tool_set_by_id", text="通常の選択ツールに戻る", icon="RESTRICT_SELECT_OFF").name = \
            "builtin.select_box"

        col = layout.column(align=True)
        col.prop(P, "brush_radius")
        col.prop(P, "brush_strength")
        row = layout.row(align=True)
        row.prop(P, "brush_selected_only", toggle=True)
        row.prop(P, "brush_xray", toggle=True)
        layout.prop(P, "comb_keep_length")
        layout.label(text="Ctrl: 反転 / Shift: スムーズ / [ ]: 半径", icon="INFO")


class VINEDRESS_PT_display(_Base, bpy.types.Panel):
    bl_label = "表示"
    bl_parent_id = "VINEDRESS_PT_tools"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_dress
        _cols(self.layout, P, ("show_overlay", "overlay_xray", "overlay_in_pose", "overlay_line_width"))


# ----------------------------------------------------------------------
class VINEDRESS_PT_auto(_Base, bpy.types.Panel):
    bl_label = "ガイド自動生成"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "gen_body_guides", toggle=True)
        row.prop(P, "gen_skirt_guides", toggle=True)
        layout.prop(P, "guide_point_spacing")
        row = layout.row()
        row.scale_y = 1.3
        row.operator("vine_dress.generate_guides", icon="OUTLINER_OB_CURVE")
        layout.label(text="VG_Body / VG_Skirt を作り直します", icon="INFO")


class VINEDRESS_PT_auto_body(_Base, bpy.types.Panel):
    bl_label = "上半身"
    bl_parent_id = "VINEDRESS_PT_auto"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_dress
        _cols(self.layout, P,
              ("vine_count", "coverage_passes", "spacing", "step_length", "max_steps"),
              ("wrap", "spiral_bias", "vertical_bias", "persistence", "noise"),
              ("branch_chance", "max_depth", "branch_radius", "taper"),
              ("radius", "surface_offset"))


class VINEDRESS_PT_auto_skirt(_Base, bpy.types.Panel):
    bl_label = "水中スカート"
    bl_parent_id = "VINEDRESS_PT_auto"
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
class VINEDRESS_PT_build(_Base, bpy.types.Panel):
    bl_label = "つる生成"

    def draw(self, context):
        P = context.scene.vine_dress
        layout = self.layout
        tree = P.groom_tree
        row = layout.row()
        row.scale_y = 1.5
        op = row.operator("vine_dress.build", icon="PLAY")
        op.tree_name = tree.name if tree else ""
        if tree is not None:
            layout.prop(tree, "auto_update", icon="FILE_REFRESH")
        layout.operator("vine_dress.open_tree", text="見た目はノードエディタで調整", icon="NODETREE")
        layout.operator("vine_dress.clear", icon="TRASH")


classes = (
    VINEDRESS_UL_groups,
    VINEDRESS_MT_group_add,
    VINEDRESS_PT_main,
    VINEDRESS_PT_heights,
    VINEDRESS_PT_groups,
    VINEDRESS_PT_tools,
    VINEDRESS_PT_display,
    VINEDRESS_PT_auto,
    VINEDRESS_PT_auto_body,
    VINEDRESS_PT_auto_skirt,
    VINEDRESS_PT_build,
)
