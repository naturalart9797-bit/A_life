bl_info = {
    "name": "Vine Grow (つる植物ジェネレータ)",
    "author": "A_life",
    "version": (1, 9, 1),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Vine Grow",
    "description": "体に打った点に密度をペイントして、体に絡みつき少し空間にも広がる、つる植物（茎・葉）を生成する。アニメーションに追従",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, surface, colonize, tangle, leaves, points, paint, mesh, binding, pipeline, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import sampler, surface, colonize, tangle, leaves, points, paint, mesh, binding, pipeline, properties, operators, ui

import bpy

_classes = properties.classes + operators.classes + paint.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vine_grow = bpy.props.PointerProperty(type=properties.VineGrowSettings)
    points.register()


def unregister():
    points.unregister()
    del bpy.types.Scene.vine_grow
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
