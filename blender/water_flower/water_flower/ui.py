# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from .operators import active_flower


def _props(layout, s, names):
    col = layout.column(align=False)
    for n in names:
        col.prop(s, n)
    return col


class _Base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "WaterFlower"


class WATERFLOWER_PT_main(_Base, bpy.types.Panel):
    bl_label = "水をためる花"
    bl_idname = "WATERFLOWER_PT_main"

    def draw(self, context):
        layout = self.layout
        layout.operator("water_flower.add", icon='OUTLINER_OB_MESH')
        layout.operator("water_flower.grow_prototype", icon='EXPERIMENTAL')
        obj = active_flower(context)
        if obj is None:
            layout.label(text="花を選ぶと設定が出ます", icon='INFO')
            return
        s = obj.water_flower
        layout.prop(s, "preset")
        layout.prop(s, "seed")
        layout.prop(s, "bloom", slider=True)
        box = layout.box()
        if s.water_volume > 0:
            box.label(text="水位 %.3f / 容量 %.4f (≈ %.2f L)" % (
                s.water_level, s.water_volume,
                s.water_volume * (context.scene.unit_settings.scale_length ** 3) * 1000.0),
                icon='MOD_FLUIDSIM')
        else:
            box.label(text="水がたまりません (縁が低すぎます)", icon='ERROR')
        row = layout.row(align=True)
        row.operator("water_flower.regenerate", icon='FILE_REFRESH')
        row.operator("water_flower.check", icon='VIEWZOOM')


class WATERFLOWER_PT_corona(_Base, bpy.types.Panel):
    bl_label = "器 (水をためる花びら)"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        _props(self.layout, s, (
            "corona_radius", "corona_height", "corona_bottom", "corona_bulge",
            "corona_flare", "corona_lobes", "corona_lobe_depth", "corona_ribs",
            "corona_rib_depth", "rim_frill", "rim_frill_freq", "rim_crimp", "rim_crimp_freq",
            "corona_res_t", "corona_res_phi"))


class WATERFLOWER_PT_petals(_Base, bpy.types.Panel):
    bl_label = "外側の花びら"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        layout = self.layout
        _props(layout, s, ("whorls", "petals_per_whorl", "petal_rotation"))
        layout.label(text="形")
        _props(layout, s, ("petal_length", "petal_width", "petal_widest",
                           "petal_pointiness", "petal_midrib", "petal_fold", "petal_reflex",
                           "petal_tip_curl", "petal_detail"))
        layout.label(text="開き方")
        _props(layout, s, ("petal_tilt", "petal_curl", "petal_cup", "petal_wrap_open",
                           "petal_attach", "tube_radius"))
        layout.label(text="あそび")
        _props(layout, s, ("petal_twist", "twist_alternate", "petal_sweep", "petal_wave", "petal_wave_freq",
                           "petal_ruffle", "petal_ruffle_freq", "petal_jitter"))
        layout.label(text="層")
        _props(layout, s, ("whorl_tilt_step", "whorl_length_step", "whorl_width_step"))
        layout.label(text="解像度")
        _props(layout, s, ("petal_res_u", "petal_res_v"))


class WATERFLOWER_PT_sepals(_Base, bpy.types.Panel):
    bl_label = "がく"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        _props(self.layout, s, ("sepal_count", "sepal_length", "sepal_width", "sepal_tilt",
                                "sepal_curl", "sepal_cup"))


class WATERFLOWER_PT_bud(_Base, bpy.types.Panel):
    bl_label = "つぼみ・アニメーション"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        obj = active_flower(context)
        s = obj.water_flower
        layout = self.layout
        layout.label(text="閉じた時の形")
        _props(layout, s, ("bud_tilt", "bud_close", "bud_spiral", "bud_scale",
                           "bud_pinch", "bud_flare", "bud_corona_radius", "bud_corona_height",
                           "sepal_bud_tilt", "anim_stagger"))
        layout.label(text="アニメーション")
        _props(layout, s, ("anim_mode", "anim_start", "anim_duration"))
        if s.anim_mode in ('CLOSE_OPEN', 'OPEN_CLOSE'):
            layout.prop(s, "anim_hold")
        layout.prop(s, "anim_samples")
        layout.operator("water_flower.bake_animation", icon='ANIM')
        if obj.data.shape_keys:
            layout.operator("water_flower.clear_animation", icon='X')
        if s.anim_stale:
            layout.label(text="設定を変えたのでアニメーションを作り直してください", icon='ERROR')


class WATERFLOWER_PT_stamen(_Base, bpy.types.Panel):
    bl_label = "しべ・茎"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        _props(self.layout, s, (
            "stamen_count", "stamen_height", "stamen_spread", "stamen_curve",
            "stamen_radius", "stamen_anther", "pistil", "pistil_height",
            "stem", "stem_length", "stem_radius", "stem_bend", "ovary_radius", "ovary_length"))


class WATERFLOWER_PT_finish(_Base, bpy.types.Panel):
    bl_label = "水・厚み・すき間"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        _props(self.layout, s, (
            "show_water", "water_fill", "water_margin", "water_gap",
            "thickness", "clearance", "whorl_gap", "overlap_gap", "smooth"))


class WATERFLOWER_PT_colors(_Base, bpy.types.Panel):
    bl_label = "色・質感"
    bl_parent_id = "WATERFLOWER_PT_main"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return active_flower(context) is not None

    def draw(self, context):
        s = active_flower(context).water_flower
        _props(self.layout, s, (
            "color_petal_base", "color_petal_tip", "color_corona_base",
            "color_corona_rim", "color_stamen", "color_filament", "color_pistil",
            "color_sepal_base", "color_sepal_tip", "color_ovary", "color_stem",
            "vein_strength", "translucency"))


classes = (
    WATERFLOWER_PT_main,
    WATERFLOWER_PT_corona,
    WATERFLOWER_PT_petals,
    WATERFLOWER_PT_sepals,
    WATERFLOWER_PT_bud,
    WATERFLOWER_PT_stamen,
    WATERFLOWER_PT_finish,
    WATERFLOWER_PT_colors,
)
