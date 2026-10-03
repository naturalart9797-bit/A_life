# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto Retopo — guide-driven automatic retopology for faces and hands."""

bl_info = {
    "name": "Auto Retopo (Face / Hand)",
    "author": "naturalart9797",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > Auto Retopo",
    "description": "Place a few guide points and get production-style face / hand topology",
    "category": "Mesh",
}

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty

from . import guides as G
from . import ops


class AutoRetopoSettings(bpy.types.PropertyGroup):
    kind: EnumProperty(
        name="Type",
        items=(('FACE', "Face", "Face mask: loops around eyes and mouth"),
               ('HAND', "Hand", "Hand: finger tubes with joint loops, palm, thumb")),
        default='FACE',
    )
    symmetric: BoolProperty(
        name="Symmetric",
        description="Face: click only one side of the paired guides; the other side is "
                    "mirrored across the plane through the centre guides, and the result "
                    "is made symmetric",
        default=True,
    )
    face_density: IntProperty(
        name="Density", default=2, min=1, max=3,
        description="1: ~720 verts, 2: ~1800 verts, 3: ~3400 verts",
    )
    iterations: IntProperty(
        name="Fit Iterations", default=80, min=5, max=300,
        description="Snap / relax iterations when fitting the face template",
    )
    hand_segments: EnumProperty(
        name="Finger Edges",
        items=(('16', "16", "16 edges around each finger (high detail)"),
               ('12', "12", "12 edges around each finger (detailed)"),
               ('8', "8", "8 edges around each finger (light)")),
        default='12',
    )
    finger_loops: FloatProperty(
        name="Finger Loop Density", default=1.0, min=0.5, max=2.0,
        description="Loops along the fingers: 1 = square quads, 2 = twice as many",
    )
    knuckle_loops: BoolProperty(
        name="Knuckle Loops", default=True,
        description="Oval loops on the back of every joint (extra edges for bending)",
    )
    forearm_loops: IntProperty(
        name="Forearm Loops", default=3, min=0, max=12,
        description="Loops continued past the wrist (only where the scan has a forearm)",
    )
    show_guides: BoolProperty(name="Show Guides", default=True)


class VIEW3D_PT_auto_retopo(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Auto Retopo"
    bl_label = "Auto Retopo"

    def draw(self, context):
        layout = self.layout
        s = context.scene.auto_retopo
        ob = context.active_object
        layout.row().prop(s, "kind", expand=True)
        if s.kind == 'FACE':
            col = layout.column(align=True)
            col.prop(s, "symmetric")
            col.prop(s, "face_density")
            col.prop(s, "iterations")
        else:
            col = layout.column(align=True)
            col.row().prop(s, "hand_segments", expand=True)
            col.prop(s, "finger_loops")
            col.prop(s, "knuckle_loops")
            col.prop(s, "forearm_loops")
        if ob is None or ob.type != 'MESH':
            layout.label(text="Select the scan / sculpt mesh", icon='INFO')
            return
        defs = G.definitions(s.kind, s.symmetric)
        g = G.load(ob, s.kind)
        done = sum(d[0] in g for d in defs)
        box = layout.box()
        box.label(text=f"{ob.name}: guides {done}/{len(defs)}",
                  icon='CHECKMARK' if done == len(defs) else 'DOT')
        col = box.column(align=True)
        if ops._GuideTool.running:
            col.label(text="Placing guides… (Enter to finish)", icon='REC')
        else:
            col.operator("object.auto_retopo_guides", icon='EMPTY_SINGLE_ARROW')
        col.operator("object.auto_retopo_clear", icon='TRASH')
        box.prop(s, "show_guides")
        row = layout.row()
        row.scale_y = 1.5
        row.enabled = done == len(defs)
        row.operator("object.auto_retopo_generate", icon='MESH_GRID')

        help_box = layout.box()
        help_box.label(text="Guides" if s.kind == 'FACE' else "Guides (back of the hand)")
        if s.kind == 'HAND':
            help_box.label(text="Wrist, then per finger: base knuckle,")
            help_box.label(text="middle joint, end joint, fingertip")
        else:
            help_box.label(text="Centre line, then eye / mouth / mask edge")
        help_box.label(text="The next guide's name is shown in the viewport")


classes = (AutoRetopoSettings, VIEW3D_PT_auto_retopo) + ops.classes


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.auto_retopo = PointerProperty(type=AutoRetopoSettings)
    ops.register_draw()


def unregister():
    ops.unregister_draw()
    del bpy.types.Scene.auto_retopo
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
