bl_info = {
    "name": "Slime Net (粘菌ネットワーク)",
    "author": "A_life",
    "version": (1, 0, 2),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Slime Net",
    "description": "オブジェクトの一点から、表面に沿って粘菌のようなネットワークを広げる。アニメーションに追従",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, network, mesh, binding, pipeline, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import sampler, network, mesh, binding, pipeline, properties, operators, ui

import bpy

_classes = properties.classes + operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.slime_net = bpy.props.PointerProperty(type=properties.SlimeNetSettings)
    bpy.types.Object.slime_reach = properties.REACH


def unregister():
    del bpy.types.Object.slime_reach
    del bpy.types.Scene.slime_net
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
