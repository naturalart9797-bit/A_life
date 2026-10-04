import bpy

from . import guides, pipeline
from .tools import TOOLS


class _Base:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Vine Wrap"


def _cols(layout, data, *groups):
    for names in groups:
        col = layout.column(align=True)
        for name in names:
            col.prop(data, name)


def _active_tool(context):
    try:
        return context.workspace.tools.from_space_view3d_mode(context.mode, create=False).idname
    except Exception:  # noqa: BLE001
        return ""


# ----------------------------------------------------------------------
class VINEWRAP_UL_guides(bpy.types.UIList):
    """Guides of the target (filtered view on bpy.data.objects)."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        gs = item.vine_guide
        row = layout.row(align=True)
        sel = item.name in context.view_layer.objects and item.select_get()
        row.prop(item, "name", text="", emboss=False,
                 icon="OUTLINER_OB_CURVE" if not gs.auto else "SHADERFX")
        sub = row.row(align=True)
        sub.alignment = "RIGHT"
        sub.prop(gs, "snap", text="", emboss=False, icon="SNAP_ON" if gs.snap else "SNAP_OFF")
        sub.prop(gs, "enabled", text="", emboss=False,
                 icon="CHECKBOX_HLT" if gs.enabled else "CHECKBOX_DEHLT")
        if sel:
            sub.label(text="", icon="RESTRICT_SELECT_OFF")

    def filter_items(self, context, data, propname):
        target = context.scene.vine_wrap.target
        objs = getattr(data, propname)
        key = self.filter_name.lower()
        flt = []
        for o in objs:
            ok = guides.is_guide_of(o, target) and (not key or key in o.name.lower())
            flt.append(self.bitflag_filter_item if ok else 0)
        order = []
        if self.use_filter_sort_alpha:
            order = bpy.types.UI_UL_list.sort_items_by_name(objs, "name")
        return flt, order


class VINEWRAP_UL_styles(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.ui_units_x = 1.0
        sub.prop(item, "color", text="")
        row.prop(item, "name", text="", emboss=False)
        target = context.scene.vine_wrap.target
        n = sum(1 for o in guides.guide_objects(target) if o.vine_guide.style_uid == item.uid) if target else 0
        row.label(text=str(n))


# ----------------------------------------------------------------------
class VINEWRAP_PT_main(_Base, bpy.types.Panel):
    bl_label = "Vine Wrap つる植物"

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        row = layout.row(align=True)
        row.prop(P, "target")
        row.operator("vine_wrap.set_target", text="", icon="EYEDROPPER")
        if P.target is None:
            layout.label(text="つるを絡ませるメッシュを選んでください", icon="INFO")
            return
        row = layout.row(align=True)
        row.scale_y = 1.5
        row.operator("vine_wrap.build", icon="PLAY")
        row.prop(P, "auto_update", text="", icon="FILE_REFRESH")
        row = layout.row(align=True)
        row.operator("vine_wrap.toggle_rest", icon="ARMATURE_DATA")
        row.operator("vine_wrap.clear", text="", icon="TRASH")


class VINEWRAP_PT_tools(_Base, bpy.types.Panel):
    bl_label = "ツール"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_wrap.target is not None

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        active = _active_tool(context)
        col = layout.column(align=True)
        col.scale_y = 1.3
        for cls in TOOLS:
            op = col.operator("wm.tool_set_by_id", text=cls.bl_label, depress=(active == cls.bl_idname))
            op.name = cls.bl_idname
        layout.operator("wm.tool_set_by_id", text="通常の選択ツール", icon="RESTRICT_SELECT_OFF").name = \
            "builtin.select_box"

        box = layout.box()
        if active == "vine_wrap.tool_curve":
            box.label(text="クリック: 点を追加")
            box.label(text="対象の上: 表面に吸着 / 外: 空間に置く")
            box.label(text="Enter・Space・右クリック・ダブルクリック: 確定")
            box.label(text="Backspace: 1つ戻す / Esc: 取り消し")
            box.label(text="選択中ガイドの端から打つ: 延長")
            box.label(text="途中でも Alt+ドラッグ / 中ボタンで視点操作")
        elif active == "vine_wrap.tool_edit":
            box.label(text="点をドラッグ: 移動")
            box.label(text="線をドラッグ: 点を追加して移動")
            box.label(text="Ctrl+クリック: 端に点を追加")
            box.label(text="X / Delete: カーソル下の点を削除")
            box.label(text="Shift+上下ドラッグ: 太さ")
            box.label(text="他のガイドをクリック: 選択 / 何もない所: 解除")
            box.prop(P, "soft_range")
        else:
            box.label(text="上のボタンでツールを選択", icon="INFO")
        layout.prop(P, "show_points")


class VINEWRAP_PT_guides(_Base, bpy.types.Panel):
    bl_label = "ガイド"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_wrap.target is not None

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        target = P.target
        n_all = len(guides.guide_objects(target))
        n_sel = sum(1 for o in context.selected_objects if guides.is_guide_of(o, target))
        layout.label(text="%d 本（選択 %d）" % (n_all, n_sel), icon="CURVE_DATA")
        layout.template_list("VINEWRAP_UL_guides", "", bpy.data, "objects", P, "active_guide_index", rows=6)

        row = layout.row(align=True)
        row.prop(P, "guide_filter", text="", icon="VIEWZOOM")
        row.operator("vine_wrap.guide_select", text="名前で選択").action = "NAME"
        row = layout.row(align=True)
        row.operator("vine_wrap.guide_select", text="全選択").action = "ALL"
        row.operator("vine_wrap.guide_select", text="解除").action = "NONE"
        row.operator("vine_wrap.guide_select", text="反転").action = "INVERT"
        row.operator("vine_wrap.guide_select", text="自動分").action = "AUTO"

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("vine_wrap.guide_edit", text="吸着", icon="SNAP_ON").action = "SNAP"
        row.operator("vine_wrap.guide_edit", text="なめらか", icon="SMOOTHCURVE").action = "SMOOTH"
        row.operator("vine_wrap.guide_edit", text="点を均等", icon="IPO_LINEAR").action = "RESAMPLE"
        row = col.row(align=True)
        row.operator("vine_wrap.guide_edit", text="向き反転", icon="ARROW_LEFTRIGHT").action = "REVERSE"
        row.operator("vine_wrap.guide_duplicate", text="複製", icon="DUPLICATE")
        row.operator("vine_wrap.guide_delete", text="削除", icon="TRASH")
        row = layout.row(align=True)
        row.operator("vine_wrap.convert_curves", icon="CURVE_BEZCURVE")
        row.operator("vine_wrap.guide_focus", text="", icon="ZOOM_SELECTED")

        act = context.active_object
        if guides.is_guide_of(act, target):
            box = layout.box()
            box.label(text=act.name, icon="OUTLINER_OB_CURVE")
            gs = act.vine_guide
            st = guides.style_of(P, act)
            row = box.row(align=True)
            row.label(text="スタイル: %s" % (st.name if st else "-"))
            row.operator("vine_wrap.style_assign", text="アクティブに変更", icon="BRUSH_DATA")
            row = box.row(align=True)
            row.prop(gs, "snap", toggle=True)
            row.prop(gs, "enabled", toggle=True)
            col = box.column(align=True)
            col.prop(gs, "radius")
            col.prop(gs, "leaves")


class VINEWRAP_PT_styles(_Base, bpy.types.Panel):
    bl_label = "スタイル（見た目）"

    @classmethod
    def poll(cls, context):
        return context.scene.vine_wrap.target is not None

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        row = layout.row()
        row.template_list("VINEWRAP_UL_styles", "", P, "styles", P, "active_style_index", rows=3)
        col = row.column(align=True)
        col.operator("vine_wrap.style_add", text="", icon="ADD")
        col.operator("vine_wrap.style_remove", text="", icon="REMOVE")
        if not len(P.styles):
            layout.label(text="ガイドを描くと「基本」スタイルが作られます")
            return
        row = layout.row(align=True)
        row.operator("vine_wrap.style_assign", icon="BRUSH_DATA")
        row.operator("vine_wrap.guide_select", text="使用中を選択").action = "STYLE"
        layout.label(text="新しく描く・生成するガイドはアクティブなスタイルになります", icon="INFO")


class VINEWRAP_PT_style_stem(_Base, bpy.types.Panel):
    bl_label = "つる"
    bl_parent_id = "VINEWRAP_PT_styles"

    @classmethod
    def poll(cls, context):
        return len(context.scene.vine_wrap.styles) > 0

    def draw(self, context):
        st = guides.active_style(context.scene.vine_wrap)
        _cols(self.layout, st, ("radius", "taper", "stem_irregular"),
              ("strands", "strand_spread", "strand_twist", "strand_radius"),
              ("noise_amp", "noise_freq"), ("tendril_density", "tendril_size"),
              ("rootlet_density", "rootlet_size"))


class VINEWRAP_PT_style_leaves(_Base, bpy.types.Panel):
    bl_label = "葉"
    bl_parent_id = "VINEWRAP_PT_styles"

    @classmethod
    def poll(cls, context):
        return len(context.scene.vine_wrap.styles) > 0

    def draw_header(self, context):
        self.layout.prop(guides.active_style(context.scene.vine_wrap), "use_leaves", text="")

    def draw(self, context):
        st = guides.active_style(context.scene.vine_wrap)
        col = self.layout.column(align=True)
        col.active = st.use_leaves
        col.prop(st, "leaf_shape")
        for name in ("leaf_density", "leaf_size", "leaf_size_var", "leaf_tip_scale", "leaf_width", "petiole"):
            col.prop(st, name)
        col = self.layout.column(align=True)
        col.active = st.use_leaves
        for name in ("leaf_tilt", "leaf_light", "leaf_curl", "leaf_wave"):
            col.prop(st, name)


class VINEWRAP_PT_style_color(_Base, bpy.types.Panel):
    bl_label = "色・質感"
    bl_parent_id = "VINEWRAP_PT_styles"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return len(context.scene.vine_wrap.styles) > 0

    def draw(self, context):
        st = guides.active_style(context.scene.vine_wrap)
        layout = self.layout
        _cols(layout, st, ("stem_young_color", "stem_color", "bark_bump"),
              ("leaf_color", "color_var", "vein_strength", "leaf_translucency", "leaf_roughness"))
        layout.label(text="葉のテクスチャ（任意・アルファ付きPNG）:")
        layout.template_ID(st, "leaf_image", open="image.open")
        layout.prop(st, "color", text="ガイドの表示色")


class VINEWRAP_PT_generate(_Base, bpy.types.Panel):
    bl_label = "自動生成"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.vine_wrap.target is not None

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        layout.prop_search(P, "gen_group", P.target, "vertex_groups")
        _cols(layout, P, ("gen_density", "gen_max", "gen_threshold"),
              ("gen_length", "gen_length_var", "gen_spacing"),
              ("gen_axis", "gen_wrap", "gen_climb", "gen_spiral_bias"))
        row = layout.row(align=True)
        row.prop(P, "gen_avoid", toggle=True)
        row.prop(P, "gen_replace", toggle=True)
        row = layout.row()
        row.scale_y = 1.5
        row.operator("vine_wrap.generate", icon="SHADERFX")
        layout.operator("vine_wrap.clear_auto", icon="TRASH")


class VINEWRAP_PT_generate_more(_Base, bpy.types.Panel):
    bl_label = "成長の詳細"
    bl_parent_id = "VINEWRAP_PT_generate"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        P = context.scene.vine_wrap
        _cols(self.layout, P, ("gen_persistence", "gen_noise", "gen_step"),
              ("gen_branch", "gen_depth", "gen_branch_radius"), ("gen_ctrl_spacing", "seed"))


class VINEWRAP_PT_settings(_Base, bpy.types.Panel):
    bl_label = "設定"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return context.scene.vine_wrap.target is not None

    def draw(self, context):
        P = context.scene.vine_wrap
        layout = self.layout
        layout.prop(P, "bind_mode")
        _cols(layout, P, ("auto_scale", "rest_pose"),
              ("ring_res", "step_length", "surface_offset", "guide_hover"))
        layout.label(text="生成物: %d オブジェクト" % len(pipeline.vine_objects(P.target)))


classes = (
    VINEWRAP_UL_guides,
    VINEWRAP_UL_styles,
    VINEWRAP_PT_main,
    VINEWRAP_PT_tools,
    VINEWRAP_PT_guides,
    VINEWRAP_PT_styles,
    VINEWRAP_PT_style_stem,
    VINEWRAP_PT_style_leaves,
    VINEWRAP_PT_style_color,
    VINEWRAP_PT_generate,
    VINEWRAP_PT_generate_more,
    VINEWRAP_PT_settings,
)
