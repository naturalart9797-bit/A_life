# SPDX-License-Identifier: GPL-3.0-or-later
"""Quad Draw — Maya-style retopology tool for Blender."""

bl_info = {
    "name": "Quad Draw (Maya style)",
    "author": "naturalart9797",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Quad Draw / Edit Mode > Mesh > Quad Draw",
    "description": "Maya Quad Draw workflow: dots, shift-fill, relax, edge loops, extend strips",
    "category": "Mesh",
}

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty

from . import tool as qd_operator


def _poll_target(_self, ob):
    return ob.type == 'MESH'


class QuadDrawSettings(bpy.types.PropertyGroup):
    target: PointerProperty(
        name="Live Surface",
        type=bpy.types.Object,
        poll=_poll_target,
        description="Reference mesh to draw on. Empty = all visible meshes except the retopo mesh",
    )
    symmetry: BoolProperty(
        name="Symmetry (X)",
        description="Mirror every operation across the retopo object's local X axis",
        default=False,
    )
    center_clip: EnumProperty(
        name="Center Clip",
        items=(('OFF', "Off", "Vertices can go anywhere"),
               ('POS', "+X", "Work on the +X half: nothing crosses X = 0, vertices near the "
                             "centre snap onto it and stay on it while dragged"),
               ('NEG', "-X", "Work on the -X half: nothing crosses X = 0, vertices near the "
                             "centre snap onto it and stay on it while dragged")),
        default='OFF',
    )
    brush_radius: IntProperty(
        name="Relax Brush", subtype='PIXEL', default=60, min=5, max=1000,
        description="Relax brush radius in pixels (B + drag to resize)",
    )
    relax_strength: FloatProperty(
        name="Relax Strength", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
    )
    strip_width: IntProperty(
        name="Strip Width", subtype='PIXEL', default=40, min=4, max=1000,
        description="Quad size in pixels for Tab + drag strips drawn on empty surface",
    )
    pick_radius: IntProperty(
        name="Pick Radius", subtype='PIXEL', default=14, min=4, max=60,
        description="Screen distance used to pick components under the cursor",
    )
    weld_distance: IntProperty(
        name="Weld Distance", subtype='PIXEL', default=20, min=1, max=200,
        description="Screen distance within which a dragged vertex / dot (or new "
                    "extend / strip vertices) snaps and merges onto another vertex",
    )
    auto_weld: BoolProperty(
        name="Auto Weld", default=True,
        description="Merge dragged vertices / new strips onto nearby vertices",
    )
    retopology_overlay: BoolProperty(
        name="Retopology Overlay", default=True,
        description="Turn on the viewport Retopology overlay when the tool starts",
    )
    show_hud: BoolProperty(name="Show Hints", default=True)


class MESH_OT_quad_draw_setup(bpy.types.Operator):
    """Make the selected mesh the live surface, create a retopo mesh and start Quad Draw"""
    bl_idname = "mesh.quad_draw_setup"
    bl_label = "New Retopo Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.area and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        src = context.active_object
        settings = context.scene.quad_draw
        if src is not None and src.type == 'MESH':
            settings.target = src
        name = (src.name if src else "Mesh") + "_retopo"
        me = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, me)
        context.collection.objects.link(ob)
        for o in context.selected_objects:
            o.select_set(False)
        ob.select_set(True)
        context.view_layer.objects.active = ob
        bpy.ops.object.mode_set(mode='EDIT')
        return bpy.ops.mesh.quad_draw('INVOKE_DEFAULT')


class MESH_OT_quad_draw_clear_dots(bpy.types.Operator):
    """Remove all unused Quad Draw dots"""
    bl_idname = "mesh.quad_draw_clear_dots"
    bl_label = "Clear Dots"

    def execute(self, context):
        qd_operator._DOTS.clear()
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class MESH_OT_quad_draw_mirror(bpy.types.Operator):
    """Copy the working half (Center Clip side, or the side with more geometry) to the
    other side and merge the vertices on the centre line"""
    bl_idname = "mesh.quad_draw_mirror"
    bl_label = "Mirror & Merge"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        ob = context.edit_object
        return ob is not None and ob.type == 'MESH'

    def execute(self, context):
        import bmesh
        from . import mesh_ops
        ob = context.edit_object
        bm = bmesh.from_edit_mesh(ob.data)
        s = context.scene.quad_draw
        side = {'POS': 1.0, 'NEG': -1.0}.get(s.center_clip) or mesh_ops.working_side(bm)
        size = max(ob.dimensions) / max(max(ob.scale), 1e-9) if ob.dimensions.length else 1.0
        n = mesh_ops.mirror_merge(bm, side, max(size * 1e-4, 1e-6))
        bmesh.update_edit_mesh(ob.data)
        qd_operator._EXTERNAL_EDITS[0] += 1
        self.report({'INFO'}, f"Mirrored {'+X' if side > 0 else '-X'} half: {n} verts")
        return {'FINISHED'}


class VIEW3D_PT_quad_draw(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Quad Draw"
    bl_label = "Quad Draw"

    def draw(self, context):
        layout = self.layout
        s = context.scene.quad_draw
        col = layout.column(align=True)
        if context.mode == 'EDIT_MESH':
            col.operator("mesh.quad_draw", icon='MESH_GRID')
        else:
            col.operator("mesh.quad_draw_setup", icon='ADD')
            col.label(text="Or enter Edit Mode on a mesh", icon='INFO')
        layout.prop(s, "target")
        layout.prop(s, "symmetry")
        layout.row().prop(s, "center_clip", expand=True)
        row = layout.row()
        row.enabled = context.mode == 'EDIT_MESH'
        row.operator("mesh.quad_draw_mirror", icon='MOD_MIRROR')
        col = layout.column(align=True)
        col.prop(s, "brush_radius")
        col.prop(s, "relax_strength")
        col = layout.column(align=True)
        col.prop(s, "strip_width")
        col.prop(s, "pick_radius")
        col.prop(s, "auto_weld")
        sub = col.row(align=True)
        sub.active = s.auto_weld
        sub.prop(s, "weld_distance")
        col = layout.column(align=True)
        col.prop(s, "retopology_overlay")
        col.prop(s, "show_hud")
        layout.operator("mesh.quad_draw_clear_dots", icon='TRASH')

        box = layout.box()
        box.label(text="Controls (Maya)")
        for line in (
            "LMB: place dot / drag to tweak",
            "Shift: fill quad · Shift+drag: relax",
            "Ctrl: insert edge loop (drag slides)",
            "Ctrl+MMB: centred edge loop",
            "Ctrl+Shift: delete (drag paints)",
            "Tab+drag edge: extend quad",
            "Tab+drag surface: draw strip",
            "Tab+MMB drag edge: extend border run",
            "B+drag: brush size",
            "M: mirror & merge centre",
            "Esc / Enter / Q: exit",
        ):
            box.label(text=line)


def _menu(self, _context):
    self.layout.separator()
    self.layout.operator("mesh.quad_draw", icon='MESH_GRID')


classes = (
    QuadDrawSettings,
    qd_operator.MESH_OT_quad_draw,
    MESH_OT_quad_draw_setup,
    MESH_OT_quad_draw_clear_dots,
    MESH_OT_quad_draw_mirror,
    VIEW3D_PT_quad_draw,
)

_keymaps = []


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.quad_draw = PointerProperty(type=QuadDrawSettings)
    bpy.types.VIEW3D_MT_edit_mesh.append(_menu)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is not None:
        km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
        kmi = km.keymap_items.new("mesh.quad_draw", 'Q', 'PRESS', shift=True, alt=True)
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    bpy.types.VIEW3D_MT_edit_mesh.remove(_menu)
    del bpy.types.Scene.quad_draw
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
