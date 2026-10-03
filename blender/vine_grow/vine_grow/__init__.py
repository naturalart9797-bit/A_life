bl_info = {
    "name": "Vine Grow (つる植物ジェネレータ)",
    "author": "A_life",
    "version": (1, 5, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Vine Grow",
    "description": "オブジェクトの一点から、体に絡みつき少し空間にも広がる、つる植物の枝を生成する。アニメーションに追従",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, surface, colonize, route, tangle, leaves, mesh, binding, pipeline, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import sampler, surface, colonize, route, tangle, leaves, mesh, binding, pipeline, properties, operators, ui

import bpy

_classes = properties.classes + operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vine_grow = bpy.props.PointerProperty(type=properties.VineGrowSettings)
    bpy.types.Object.vine_grow_reach = properties.REACH
    bpy.types.Object.vine_grow_density = properties.DENSITY
    bpy.types.Object.vine_grow_point = bpy.props.PointerProperty(type=properties.VinePointSettings)
    bpy.types.Object.vine_grow_custom = properties.CUSTOM


def unregister():
    del bpy.types.Object.vine_grow_custom
    del bpy.types.Object.vine_grow_point
    del bpy.types.Object.vine_grow_density
    del bpy.types.Object.vine_grow_reach
    del bpy.types.Scene.vine_grow
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
