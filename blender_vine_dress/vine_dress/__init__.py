bl_info = {
    "name": "Vine Dress (つる植物ドレス)",
    "author": "A_life",
    "version": (1, 0, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Vine Dress",
    "description": "人物メッシュにつる植物を服のように纏わせ、水中部分をスカート状に広げる。アニメーションに追従",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, growth, skirt, meshgen, binding, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import sampler, growth, skirt, meshgen, binding, properties, operators, ui

import bpy

_classes = (properties.VineDressSettings,) + operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vine_dress = bpy.props.PointerProperty(type=properties.VineDressSettings)


def unregister():
    del bpy.types.Scene.vine_dress
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
