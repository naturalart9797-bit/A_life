# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


class WATERFLOWER_PT_main(bpy.types.Panel):
    bl_label = "成長する花"
    bl_idname = "WATERFLOWER_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "WaterFlower"

    def draw(self, context):
        layout = self.layout
        layout.operator("water_flower.grow_prototype", icon='EXPERIMENTAL')
        col = layout.column(align=True)
        col.label(text="脈と二層の膜の成長から形を計算します")
        col.label(text="下書きで約 1 分かかります")


classes = (WATERFLOWER_PT_main,)
