# UV Toolkit panels (UV Editor sidebar), header buttons, marking menus (pies)
# and the 3D Viewport UV panel.

import bpy

from .common import settings, addon_prefs, is_sync, op_available


def _is_uv_editor(context):
    sp = context.space_data
    return (context.mode == 'EDIT_MESH' and sp is not None and sp.type == 'IMAGE_EDITOR'
            and getattr(sp, "mode", 'UV') == 'UV')


class _UVPanel:
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "UV Toolkit"

    @classmethod
    def poll(cls, context):
        return _is_uv_editor(context)


class _SubPanel(_UVPanel):
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"


def _row_ops(layout, items, align=True):
    row = layout.row(align=align)
    for idname, text, props in items:
        op = row.operator(idname, text=text)
        for k, v in props.items():
            setattr(op, k, v)
    return row


def _component_mode(context):
    ts = context.scene.tool_settings
    if getattr(ts, "use_uv_select_island", False) or ts.uv_select_mode == 'ISLAND':
        return 'SHELL'
    if is_sync(context):
        v, e, f = ts.mesh_select_mode
        return 'VERTEX' if v else ('EDGE' if e else 'FACE')
    return ts.uv_select_mode


class IMAGE_PT_maya_uv_toolkit(_UVPanel, bpy.types.Panel):
    bl_label = "UV Toolkit"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        current = _component_mode(context)
        row = layout.row(align=True)
        for mode, text, icon in (('VERTEX', "UV", 'UV_VERTEXSEL'), ('EDGE', "Edge", 'UV_EDGESEL'),
                                 ('FACE', "Face", 'UV_FACESEL'), ('SHELL', "Shell", 'UV_ISLANDSEL')):
            row.operator("uv.maya_component_mode", text=text, icon=icon,
                         depress=current == mode).mode = mode
        row = layout.row(align=True)
        row.operator("maya_uv.checker", text="Checker", icon='TEXTURE', depress=s.checker_on)
        row.prop(context.space_data.uv_editor, "show_stretch", text="Distortion", toggle=True)
        row = layout.row(align=True)
        row.prop(s, "show_texture_borders", text="Borders", toggle=True)
        row.operator("uv.maya_isolate", text="Isolate", depress=s.isolate)
        row.prop(context.scene.tool_settings, "use_uv_select_sync", text="Sync", toggle=True)


class IMAGE_PT_maya_uv_pin(_SubPanel, bpy.types.Panel):
    bl_label = "Pin"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("uv.pin", text="Pin", icon='PINNED').clear = False
        row.operator("uv.pin", text="Unpin", icon='UNPINNED').clear = True
        if "invert" in bpy.ops.uv.pin.get_rna_type().properties:
            row.operator("uv.pin", text="Invert").invert = True
        row = layout.row(align=True)
        row.operator("uv.maya_unpin_all", text="Unpin All")
        row.operator("uv.select_pinned", text="Select Pinned")


class IMAGE_PT_maya_uv_select(_SubPanel, bpy.types.Panel):
    bl_label = "Select"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Convert")
        _row_ops(col, [("uv.maya_convert_selection", "UV", {"target": 'VERTEX'}),
                       ("uv.maya_convert_selection", "Edge", {"target": 'EDGE'}),
                       ("uv.maya_convert_selection", "Face", {"target": 'FACE'})])
        _row_ops(col, [("uv.maya_convert_selection", "Shell", {"target": 'SHELL'}),
                       ("uv.maya_convert_selection", "Shell Border", {"target": 'BORDER'})])

        col = layout.column(align=True)
        col.label(text="Select by Type")
        _row_ops(col, [("uv.maya_select_by_type", "Texture Borders", {"type": 'BORDERS'}),
                       ("uv.select_overlap", "Overlapping", {})])
        _row_ops(col, [("uv.maya_select_by_type", "Back-Facing", {"type": 'BACKFACING'}),
                       ("uv.maya_select_by_type", "Front-Facing", {"type": 'FRONTFACING'})])
        _row_ops(col, [("uv.maya_select_by_type", "Unmapped", {"type": 'UNMAPPED'})])
        col.operator_menu_enum("uv.select_similar", "type", text="Similar")

        col = layout.column(align=True)
        _row_ops(col, [("uv.select_more", "Grow", {}), ("uv.select_less", "Shrink", {})])
        row = col.row(align=True)
        row.operator("uv.select_all", text="Invert").action = 'INVERT'
        row.operator("uv.select_all", text="All").action = 'SELECT'

        ts = context.scene.tool_settings
        row = layout.row(align=True)
        row.prop(ts, "use_proportional_edit", text="Soft Select", toggle=True)
        row.prop(ts, "proportional_edit_falloff", text="", icon_only=True)
        layout.label(text="Double-click: Shell  ·  Shift+Double-click: Path", icon='INFO')


class IMAGE_PT_maya_uv_tools(_SubPanel, bpy.types.Panel):
    bl_label = "Tools"

    def draw(self, context):
        grid = self.layout.grid_flow(columns=3, align=True)
        for tool, text, icon in (('SELECT', "Select", 'RESTRICT_SELECT_OFF'), ('MOVE', "Move", 'ORIENTATION_VIEW'),
                                 ('ROTATE', "Rotate", 'FILE_REFRESH'), ('SCALE', "Scale", 'FULLSCREEN_ENTER'),
                                 ('CUT', "Cut", 'SCULPTMODE_HLT'), ('GRAB', "Grab", 'VIEW_PAN'),
                                 ('UNFOLD', "Unfold", 'MOD_SMOOTH'), ('PINCH', "Pinch", 'PIVOT_MEDIAN')):
            grid.operator("uv.maya_tool", text=text, icon=icon).tool = tool


class IMAGE_PT_maya_uv_transform(_SubPanel, bpy.types.Panel):
    bl_label = "Transform"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        layout.row().prop(s, "pivot", expand=True)

        # Move arrows
        box = layout.box()
        grid = box.grid_flow(columns=3, align=True)
        grid.label(text="")
        grid.operator("uv.maya_move", text="", icon='TRIA_UP').direction = 'UP'
        grid.label(text="")
        grid.operator("uv.maya_move", text="", icon='TRIA_LEFT').direction = 'LEFT'
        grid.prop(s, "move_step", text="")
        grid.operator("uv.maya_move", text="", icon='TRIA_RIGHT').direction = 'RIGHT'
        grid.label(text="")
        grid.operator("uv.maya_move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        grid.label(text="")

        # Rotate / scale / flip
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("uv.maya_rotate", text="", icon='LOOP_FORWARDS').angle = -s.rotate_angle
        row.prop(s, "rotate_angle", text="Rotate")
        row.operator("uv.maya_rotate", text="", icon='LOOP_BACK').angle = s.rotate_angle
        row = col.row(align=True)
        op = row.operator("uv.maya_scale", text="", icon='REMOVE')
        op.factor, op.axis = 1.0 / s.scale_factor, 'UV'
        row.prop(s, "scale_factor", text="Scale")
        op = row.operator("uv.maya_scale", text="", icon='ADD')
        op.factor, op.axis = s.scale_factor, 'UV'
        row = col.row(align=True)
        for axis in ('U', 'V'):
            op = row.operator("uv.maya_scale", text=f"−{axis}")
            op.factor, op.axis = 1.0 / s.scale_factor, axis
            op = row.operator("uv.maya_scale", text=f"+{axis}")
            op.factor, op.axis = s.scale_factor, axis
        _row_ops(col, [("uv.maya_flip", "Flip U", {"axis": 'U'}),
                       ("uv.maya_flip", "Flip V", {"axis": 'V'})])

        # Align
        col = layout.column(align=True)
        col.label(text="Align (Shell pivot aligns whole shells)")
        for axis in ('U', 'V'):
            _row_ops(col, [("uv.maya_align", f"{label} {axis}", {"axis": axis, "mode": mode})
                           for mode, label in (('MIN', "Min"), ('MEAN', "Avg"), ('MAX', "Max"))])

        # Snap to tile (3 x 3)
        col = layout.column(align=True)
        col.label(text="Snap to Tile")
        for v in (1.0, 0.5, 0.0):
            row = col.row(align=True)
            for u in (0.0, 0.5, 1.0):
                icon = 'DOT' if (u, v) == (0.5, 0.5) else 'SNAP_FACE_CENTER' if 0.5 in (u, v) else 'SNAP_VERTEX'
                op = row.operator("uv.maya_snap_tile", text="", icon=icon)
                op.u, op.v = u, v

        # Straighten / orient / match / snap
        col = layout.column(align=True)
        col.label(text="Straighten")
        _row_ops(col, [("uv.align", "UVs", {"axis": 'ALIGN_S'}),
                       ("uv.align", "U", {"axis": 'ALIGN_T'}),
                       ("uv.align", "V", {"axis": 'ALIGN_U'})])
        row = col.row(align=True)
        row.operator("uv.maya_orient_shells", text="Orient Shells")
        if op_available("uv.align_rotation"):
            row.operator("uv.align_rotation", text="Orient to Edge").method = 'EDGE'

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("uv.remove_doubles", text="Match UVs").threshold = s.match_tolerance
        row.prop(s, "match_tolerance", text="")
        row = col.row(align=True)
        row.operator("uv.snap_selected", text="Snap to Pixels").target = 'PIXELS'
        row.operator("uv.randomize_uv_transform", text="Randomize")
        row = col.row(align=True)
        row.operator("uv.maya_normalize", text="Normalize")
        row.prop(s, "normalize_keep_aspect", text="Keep Aspect", toggle=True)
        col.operator("uv.maya_unitize", text="Unitize")


def draw_create(layout):
    col = layout.column(align=True)
    col.label(text="Planar")
    _row_ops(col, [("uv.maya_planar", axis, {"axis": axis}) for axis in ('X', 'Y', 'Z')]
             + [("uv.maya_planar", "Best", {"axis": 'BEST'})])
    col = layout.column(align=True)
    col.operator("uv.maya_view_project", text="Camera-based").kind = 'CAMERA'
    _row_ops(col, [("uv.maya_view_project", "Cylindrical", {"kind": 'CYLINDER'}),
                   ("uv.maya_view_project", "Spherical", {"kind": 'SPHERE'})])
    _row_ops(col, [("uv.cube_project", "Cube", {}), ("uv.smart_project", "Automatic", {})])


class IMAGE_PT_maya_uv_create(_SubPanel, bpy.types.Panel):
    bl_label = "Create"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_create(self.layout)


class IMAGE_PT_maya_uv_cut_sew(_SubPanel, bpy.types.Panel):
    bl_label = "Cut and Sew"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("uv.mark_seam", text="Cut", icon='SCULPTMODE_HLT').clear = False
        row.operator("uv.maya_sew", text="Sew").move = False
        row.operator("uv.maya_sew", text="Move and Sew").move = True
        row = col.row(align=True)
        row.operator("uv.select_split", text="Split")
        row.operator("uv.maya_create_shell", text="Create Shell").grid = False
        row.operator("uv.maya_create_shell", text="Grid").grid = True
        row = col.row(align=True)
        row.operator("uv.seams_from_islands", text="Seams from Shells")
        row.operator("uv.mark_seam", text="Clear Seams").clear = True
        row = layout.row(align=True)
        row.operator("uv.maya_auto_seams", text="Auto Seams")
        row.prop(s, "auto_seam_angle", text="")


class IMAGE_PT_maya_uv_unfold(_SubPanel, bpy.types.Panel):
    bl_label = "Unfold"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        col = layout.column(align=True)
        col.operator("uv.maya_unfold", text="Unfold", icon='MOD_UVPROJECT')
        col.row(align=True).prop(s, "unfold_constraint", expand=True)
        col.prop(s, "unfold_method", text="")
        row = layout.row(align=True)
        row.operator("uv.maya_optimize", text="Optimize")
        row.prop(s, "optimize_iterations", text="")
        row = layout.row(align=True)
        row.operator("uv.maya_straighten_shell", text="Straighten Shell")
        row.operator("uv.maya_tool", text="Unfold Brush").tool = 'UNFOLD'


class IMAGE_PT_maya_uv_layout(_SubPanel, bpy.types.Panel):
    bl_label = "Arrange and Layout"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        col = layout.column(align=True)
        col.operator("uv.maya_layout", text="Layout", icon='UV_ISLANDSEL')
        col.prop(s, "layout_margin")
        row = col.row(align=True)
        row.prop(s, "layout_rotate", toggle=True)
        row.prop(s, "layout_scale", toggle=True)
        col.prop(s, "layout_shape", text="")

        col = layout.column(align=True)
        col.label(text="Distribute")
        row = col.row(align=True)
        row.operator("uv.maya_distribute", text="U").axis = 'U'
        row.operator("uv.maya_distribute", text="V").axis = 'V'
        row.prop(s, "distribute_spacing", text="Spacing")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator("uv.maya_stack", text="Stack").orient = False
        row.operator("uv.maya_stack", text="Stack & Orient").orient = True
        row = col.row(align=True)
        row.operator("uv.maya_stack_similar", text="Stack Similar")
        row.operator("uv.maya_layout", text="Unstack")
        row = col.row(align=True)
        row.operator("uv.maya_gather", text="Gather Shells")
        row.operator("uv.average_islands_scale", text="Normalize Scale")


class IMAGE_PT_maya_uv_measure(_SubPanel, bpy.types.Panel):
    bl_label = "Measure"

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.prop(s, "texture_size_preset", text="")
        row.prop(s, "texture_size", text="")
        row = layout.row(align=True)
        row.prop(s, "texel_density", text="px/m")
        row.operator("uv.maya_texel_get", text="Get")
        row.operator("uv.maya_texel_set", text="Set")
        layout.operator("uv.maya_measure", text="Measure Selection", icon='DRIVER_DISTANCE')
        if s.measure_text:
            col = layout.box().column(align=True)
            for line in s.measure_text.split("\n"):
                col.label(text=line)


class IMAGE_PT_maya_uv_sets(_SubPanel, bpy.types.Panel):
    bl_label = "UV Sets"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        me = context.edit_object.data
        row = layout.row()
        row.template_list("MESH_UL_uvmaps", "", me, "uv_layers", me.uv_layers, "active_index", rows=3)
        col = row.column(align=True)
        col.operator("mesh.uv_texture_add", icon='ADD', text="")
        col.operator("mesh.uv_texture_remove", icon='REMOVE', text="")
        layout.operator("uv.maya_copy_uv_set", text="Copy UVs to UV Set", icon='COPYDOWN')
        if op_available("uv.export_layout"):
            layout.operator("uv.export_layout", text="UV Snapshot", icon='EXPORT')


class IMAGE_PT_maya_uv_display(_SubPanel, bpy.types.Panel):
    bl_label = "Display"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        uv_editor = context.space_data.uv_editor
        col = layout.column(align=True)
        col.prop(s, "checker_type", text="")
        col.prop(s, "checker_tiles")
        col = layout.column(align=True)
        col.prop(uv_editor, "display_stretch_type", text="Distortion")
        col.prop(s, "border_width")
        col.prop(uv_editor, "show_faces", text="Shaded UVs")


def draw_header(self, context):
    if not _is_uv_editor(context):
        return
    s = settings(context)
    row = self.layout.row(align=True)
    row.operator("maya_uv.checker", text="", icon='TEXTURE', depress=s.checker_on)
    row.prop(context.space_data.uv_editor, "show_stretch", text="", icon='MOD_UVPROJECT')
    row.prop(s, "show_texture_borders", text="", icon='MESH_PLANE')
    row.operator("uv.maya_isolate", text="", icon='HIDE_OFF', depress=s.isolate)


# ---------------------------------------------------------------------------
# Marking menus (pie menus)
# ---------------------------------------------------------------------------

class MAYA_MT_uv_component_pie(bpy.types.Menu):
    bl_label = "Component"

    def draw(self, context):
        pie = self.layout.menu_pie()
        # Order: W, E, S, N, NW, NE, SW, SE
        pie.operator("uv.maya_component_mode", text="UV", icon='UV_VERTEXSEL').mode = 'VERTEX'
        pie.operator("uv.maya_component_mode", text="UV Shell", icon='UV_ISLANDSEL').mode = 'SHELL'
        pie.operator("uv.maya_component_mode", text="Face", icon='UV_FACESEL').mode = 'FACE'
        pie.operator("uv.maya_component_mode", text="Edge", icon='UV_EDGESEL').mode = 'EDGE'
        pie.operator("uv.select_all", text="Select All").action = 'SELECT'
        pie.operator("uv.maya_isolate", text="Isolate Select", icon='HIDE_OFF')
        pie.prop(context.scene.tool_settings, "use_uv_select_sync", text="Sync Selection")
        pie.operator("uv.select_all", text="Invert Selection").action = 'INVERT'


class MAYA_MT_uv_tools_pie(bpy.types.Menu):
    bl_label = "UV Tools"

    def draw(self, context):
        pie = self.layout.menu_pie()
        pie.operator("uv.mark_seam", text="Cut", icon='SCULPTMODE_HLT').clear = False
        pie.operator("uv.maya_sew", text="Sew").move = False
        pie.operator("uv.maya_layout", text="Layout", icon='UV_ISLANDSEL')
        pie.operator("uv.maya_unfold", text="Unfold", icon='MOD_UVPROJECT')
        pie.operator("uv.maya_optimize", text="Optimize")
        pie.operator("uv.maya_sew", text="Move and Sew").move = True
        pie.operator("uv.align", text="Straighten UVs").axis = 'ALIGN_S'
        pie.operator("uv.maya_orient_shells", text="Orient Shells")


class MAYA_MT_uv_convert_pie(bpy.types.Menu):
    bl_label = "Convert Selection"

    def draw(self, context):
        pie = self.layout.menu_pie()
        pie.operator("uv.maya_convert_selection", text="To UVs").target = 'VERTEX'
        pie.operator("uv.maya_convert_selection", text="To Edges").target = 'EDGE'
        pie.operator("uv.maya_convert_selection", text="To Faces").target = 'FACE'
        pie.operator("uv.maya_convert_selection", text="To Shell").target = 'SHELL'
        pie.operator("uv.maya_convert_selection", text="To Shell Border").target = 'BORDER'
        pie.operator("uv.select_more", text="Grow")
        pie.operator("uv.select_less", text="Shrink")
        pie.operator("uv.maya_select_by_type", text="Texture Borders").type = 'BORDERS'


class UV_OT_maya_marking_menu(bpy.types.Operator):
    """Open a Maya-style marking menu"""
    bl_idname = "uv.maya_marking_menu"
    bl_label = "Marking Menu"

    menu: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        prefs = addon_prefs(context)
        # Failing poll lets the click fall through to Blender's own binding.
        return (prefs is None or prefs.use_marking_menus) and _is_uv_editor(context)

    def invoke(self, context, event):
        bpy.ops.wm.call_menu_pie(name=self.menu)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 3D Viewport
# ---------------------------------------------------------------------------

class VIEW3D_PT_maya_uv(bpy.types.Panel):
    bl_label = "UV"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Maya"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        s = settings(context)
        layout = self.layout
        layout.prop(s, "shell_select_3d", toggle=True, icon='UV_ISLANDSEL')
        row = layout.row(align=True)
        row.operator("maya_uv.open_editor", icon='UV')
        row.operator("maya_uv.checker", text="Checker", icon='TEXTURE', depress=s.checker_on)
        row = layout.row(align=True)
        row.operator("mesh.mark_seam", text="Cut").clear = False
        row.operator("mesh.mark_seam", text="Uncut").clear = True
        row = layout.row(align=True)
        row.operator("uv.maya_auto_seams", text="Auto Seams")
        row.prop(s, "auto_seam_angle", text="")
        draw_create(layout)


classes = (
    IMAGE_PT_maya_uv_toolkit,
    IMAGE_PT_maya_uv_pin,
    IMAGE_PT_maya_uv_select,
    IMAGE_PT_maya_uv_tools,
    IMAGE_PT_maya_uv_transform,
    IMAGE_PT_maya_uv_create,
    IMAGE_PT_maya_uv_cut_sew,
    IMAGE_PT_maya_uv_unfold,
    IMAGE_PT_maya_uv_layout,
    IMAGE_PT_maya_uv_measure,
    IMAGE_PT_maya_uv_sets,
    IMAGE_PT_maya_uv_display,
    MAYA_MT_uv_component_pie,
    MAYA_MT_uv_tools_pie,
    MAYA_MT_uv_convert_pie,
    UV_OT_maya_marking_menu,
    VIEW3D_PT_maya_uv,
)
