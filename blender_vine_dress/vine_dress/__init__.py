bl_info = {
    "name": "Vine Dress (つる植物ドレス)",
    "author": "A_life",
    "version": (2, 0, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Vine Dress / ノードエディタ > Vine Groom",
    "description": "人物にガイドカーブをグルーミングし、ノードグラフでつる植物の服と水中スカートを生成する",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, growth, skirt, meshgen, binding, guides, build, groom_tree,  # noqa: F821
               pipeline, overlay, tools, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import (sampler, growth, skirt, meshgen, binding, guides, build, groom_tree,
                   pipeline, overlay, tools, properties, operators, ui)

import bpy

_classes = ((guides.GuideSettings, properties.VineDressSettings) + groom_tree.classes
            + tools.classes + operators.classes + ui.classes)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vine_dress = bpy.props.PointerProperty(type=properties.VineDressSettings)
    bpy.types.Object.vine_guide = bpy.props.PointerProperty(type=guides.GuideSettings)
    bpy.types.NODE_MT_add.append(groom_tree.draw_add_menu)
    tools.register_tools()
    if not bpy.app.background:
        overlay.register()


def unregister():
    overlay.unregister()
    tools.unregister_tools()
    bpy.types.NODE_MT_add.remove(groom_tree.draw_add_menu)
    del bpy.types.Object.vine_guide
    del bpy.types.Scene.vine_dress
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
