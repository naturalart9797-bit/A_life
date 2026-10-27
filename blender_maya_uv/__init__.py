# Maya-style UV Toolkit for Blender's UV Editor.
#
# UV Editor > Sidebar (N) > "UV Toolkit" follows the sections of Maya's UV
# Toolkit: Pin / Select / Tools / Transform / Create / Cut and Sew / Unfold /
# Arrange and Layout / Measure / UV Sets. The UV Editor also gets Maya's
# display toggles in its header, double-click shell select, Shift + double-click
# path select and marking menus (right-click, Shift / Ctrl + right-click).

bl_info = {
    "name": "Maya Style UV Toolkit",
    "author": "naturalart9797",
    "version": (2, 1, 0),
    "blender": (3, 0, 0),
    "location": "UV Editor > Sidebar (N) > UV Toolkit, View3D > Sidebar (N) > Maya > UV",
    "description": "Maya's UV Toolkit, marking menus and UV editor display toggles",
    "category": "UV",
}

import bpy

from . import common, display, ops_select, ops_transform, ops_uv, shell_select, ui


class MayaUVPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    use_marking_menus: bpy.props.BoolProperty(
        name="Marking Menus",
        description="Right-click: components, Shift + right-click: UV tools, "
                    "Ctrl + right-click: convert selection (UV Editor, left-click select only). "
                    "Turn off to get Blender's right-click context menu back",
        default=True)

    def draw(self, context):
        self.layout.prop(self, "use_marking_menus")


_keymaps = []


def _select_mouse():
    prefs = getattr(bpy.context.window_manager.keyconfigs.active, "preferences", None)
    return 'RIGHTMOUSE' if getattr(prefs, "select_mouse", 'LEFT') == 'RIGHT' else 'LEFTMOUSE'


def _register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    select_mouse = _select_mouse()
    km = kc.keymaps.new(name="UV Editor", space_type='EMPTY')

    def add(idname, key, value, **kwargs):
        props = kwargs.pop("props", {})
        kmi = km.keymap_items.new(idname, key, value, **kwargs)
        for k, v in props.items():
            setattr(kmi.properties, k, v)
        _keymaps.append((km, kmi))

    # DOUBLE_CLICK is matched before being retried as PRESS, so these win over
    # the active tool's click-select.
    add("uv.select_linked_pick", select_mouse, 'DOUBLE_CLICK',
        props={"extend": False, "deselect": False})
    add(ops_select.UV_OT_maya_path_select.bl_idname, select_mouse, 'DOUBLE_CLICK', shift=True)

    # Marking menus need the right button to be free (left-click select).
    if select_mouse == 'LEFTMOUSE':
        menu = ui.UV_OT_maya_marking_menu.bl_idname
        add(menu, 'RIGHTMOUSE', 'PRESS', props={"menu": "MAYA_MT_uv_component_pie"})
        add(menu, 'RIGHTMOUSE', 'PRESS', shift=True, props={"menu": "MAYA_MT_uv_tools_pie"})
        add(menu, 'RIGHTMOUSE', 'PRESS', ctrl=True, props={"menu": "MAYA_MT_uv_convert_pie"})


classes = (
    MayaUVPreferences,
    common.MayaUVSettings,
    *ops_select.classes,
    *ops_transform.classes,
    *ops_uv.classes,
    *display.classes,
    *ui.classes,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.maya_uv = bpy.props.PointerProperty(type=common.MayaUVSettings)
    bpy.types.IMAGE_HT_header.append(ui.draw_header)
    bpy.app.handlers.depsgraph_update_post.append(ops_select.track_uv_active)
    bpy.app.handlers.depsgraph_update_post.append(shell_select.on_depsgraph_update)
    display.register_draw()
    _register_keymaps()


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    display.unregister_draw()
    if shell_select.on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(shell_select.on_depsgraph_update)
    if ops_select.track_uv_active in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(ops_select.track_uv_active)
    bpy.types.IMAGE_HT_header.remove(ui.draw_header)
    del bpy.types.Scene.maya_uv
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    ops_select.clear_state()
    shell_select.clear_state()
