# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "Water Flower (水をためる花)",
    "author": "A_life",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > 追加 > メッシュ > 水をためる花 / サイドバー(N) > WaterFlower",
    "description": "中央の器に水がたまる花を生成する。外側の花びらはねじれ・うねり、つぼみに閉じるアニメーションも作れる",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (geometry, properties, build, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import geometry, properties, build, operators, ui

import bpy

_classes = properties.classes + operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.water_flower = bpy.props.PointerProperty(type=properties.WaterFlowerSettings)
    bpy.types.VIEW3D_MT_mesh_add.append(operators.menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(operators.menu_func)
    del bpy.types.Object.water_flower
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
