bl_info = {
    "name": "Vine Wrap (つる植物を絡ませる)",
    "author": "A_life",
    "version": (1, 2, 0),
    "blender": (3, 6, 0),
    "location": "3Dビューポート > サイドバー(N) > Vine Wrap / ツールバー",
    "description": "クリックでガイドカーブを作る・編集する・自動生成して、オブジェクトにつる植物を絡ませる",
    "category": "Add Mesh",
}

if "bpy" in locals():
    import importlib
    for _m in (sampler, growth, guides, build, meshgen, binding, pipeline, overlay,  # noqa: F821
               tools, properties, operators, ui):  # noqa: F821
        importlib.reload(_m)
else:
    from . import (sampler, growth, guides, build, meshgen, binding, pipeline, overlay,
                   tools, properties, operators, ui)

import bpy

_classes = properties.classes + tools.classes + operators.classes + ui.classes


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.vine_wrap = bpy.props.PointerProperty(type=properties.VineWrapSettings)
    bpy.types.Object.vine_guide = bpy.props.PointerProperty(type=properties.GuideSettings)
    tools.register_tools()
    pipeline.register()
    if not bpy.app.background:
        overlay.register()


def unregister():
    overlay.unregister()
    pipeline.unregister()
    tools.unregister_tools()
    del bpy.types.Object.vine_guide
    del bpy.types.Scene.vine_wrap
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
