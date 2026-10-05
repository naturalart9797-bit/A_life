# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "Water Flower (成長する花)",
    "author": "A_life",
    "version": (3, 0, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > 追加 > メッシュ / サイドバー(N) > WaterFlower",
    "description": "花びらを、脈と二層の膜の成長から力学計算で作る (試作)",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (growth, materials, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import growth, materials, operators, ui

import bpy

_classes = operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(operators.menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(operators.menu_func)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
