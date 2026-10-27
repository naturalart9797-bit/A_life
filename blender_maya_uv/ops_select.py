# Select section of the UV Toolkit: component modes, selection conversion,
# select by type (shell border, back-facing, unmapped), isolate, pins and the
# Shift + double-click shortest path.

import bpy
import bmesh
from bpy.app.handlers import persistent

from .common import (
    UVOperator, EPS, settings, is_sync, edit_meshes, update, islands, selected_islands,
    is_border_loop, uv_area, set_selection, op_available,
)


# ---------------------------------------------------------------------------
# Component mode / conversion
# ---------------------------------------------------------------------------

def _set_uv_mode(context, mode):
    """Switch the UV component mode, converting the selection like Maya does."""
    ts = context.scene.tool_settings
    if is_sync(context):
        mesh_mode = {'VERTEX': 'VERT', 'EDGE': 'EDGE', 'FACE': 'FACE'}[mode]
        bpy.ops.mesh.select_mode(type=mesh_mode)
        return
    if hasattr(ts, "use_uv_select_island"):
        ts.use_uv_select_island = False
    if op_available("uv.select_mode") and bpy.ops.uv.select_mode.poll():
        bpy.ops.uv.select_mode(type=mode)
    else:
        ts.uv_select_mode = mode


def _set_shell_mode(context):
    ts = context.scene.tool_settings
    if hasattr(ts, "use_uv_select_island"):  # Blender 5.0+
        if not is_sync(context) and ts.uv_select_mode == 'VERTEX':
            _set_uv_mode(context, 'FACE')
        ts.use_uv_select_island = True
    elif 'ISLAND' in {i.identifier for i in ts.bl_rna.properties['uv_select_mode'].enum_items}:
        ts.uv_select_mode = 'ISLAND'
    bpy.ops.uv.select_linked()


class UV_OT_maya_component_mode(UVOperator, bpy.types.Operator):
    """Switch component mode (Maya: UV / Edge / Face / UV Shell)"""
    bl_idname = "uv.maya_component_mode"
    bl_label = "Component Mode"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items=[('VERTEX', "UV", "UV (vertex) selection"),
               ('EDGE', "Edge", "Edge selection"),
               ('FACE', "Face", "Face selection"),
               ('SHELL', "UV Shell", "Whole shell selection")])

    def execute(self, context):
        if self.mode == 'SHELL':
            _set_shell_mode(context)
        else:
            _set_uv_mode(context, self.mode)
        return {'FINISHED'}


def _select_borders(context, only_selected_shells):
    sync = is_sync(context)
    # Remember the border loops by index: the deselect below runs an operator.
    borders = {}
    for ob, bm, uv, faces in edit_meshes(context):
        shells = selected_islands(faces, uv, sync) if only_selected_shells else islands(faces, uv)
        border = []
        for isl in shells:
            face_set = set(isl)
            border.extend((f.index, i) for f in isl for i, l in enumerate(f.loops)
                          if is_border_loop(l, uv, face_set))
        borders[ob.name] = border
    bpy.ops.uv.select_all(action='DESELECT')
    for ob, bm, uv, faces in edit_meshes(context):
        bm.faces.ensure_lookup_table()
        edges = [bm.faces[fi].loops[i] for fi, i in borders.get(ob.name, ())]
        set_selection(context, bm, uv, faces, edges=edges)
        update(ob)


class UV_OT_maya_convert_selection(UVOperator, bpy.types.Operator):
    """Convert the selection (Maya: Select > Convert Selection)"""
    bl_idname = "uv.maya_convert_selection"
    bl_label = "Convert Selection"

    target: bpy.props.EnumProperty(
        items=[('VERTEX', "To UVs", ""),
               ('EDGE', "To Edges", ""),
               ('FACE', "To Faces", ""),
               ('SHELL', "To Shell", ""),
               ('BORDER', "To Shell Border", "")])

    def execute(self, context):
        if self.target == 'SHELL':
            bpy.ops.uv.select_linked()
        elif self.target == 'BORDER':
            if is_sync(context) or context.scene.tool_settings.uv_select_mode == 'VERTEX':
                _set_uv_mode(context, 'EDGE')
            _select_borders(context, only_selected_shells=True)
        else:
            _set_uv_mode(context, self.target)
        return {'FINISHED'}


class UV_OT_maya_select_by_type(UVOperator, bpy.types.Operator):
    """Select UVs by type (Maya: Select > Select by Type)"""
    bl_idname = "uv.maya_select_by_type"
    bl_label = "Select by Type"

    type: bpy.props.EnumProperty(
        items=[('BORDERS', "Texture Borders", "Every shell border"),
               ('BACKFACING', "Back-Facing", "Faces whose UVs are flipped"),
               ('FRONTFACING', "Front-Facing", "Faces whose UVs are not flipped"),
               ('UNMAPPED', "Unmapped", "Faces with zero UV area")])

    def execute(self, context):
        if self.type == 'BORDERS':
            if is_sync(context) or context.scene.tool_settings.uv_select_mode == 'VERTEX':
                _set_uv_mode(context, 'EDGE')
            _select_borders(context, only_selected_shells=False)
            return {'FINISHED'}
        bpy.ops.uv.select_all(action='DESELECT')
        count = 0
        for ob, bm, uv, faces in edit_meshes(context):
            if self.type == 'UNMAPPED':
                hit = [f for f in faces if uv_area(f, uv) < EPS * EPS]
            else:
                flipped = self.type == 'BACKFACING'
                hit = [f for f in faces if uv_area(f, uv) >= EPS * EPS
                       and (uv_area(f, uv, signed=True) < 0.0) == flipped]
            set_selection(context, bm, uv, faces, select_faces=hit)
            count += len(hit)
            update(ob)
        self.report({'INFO'}, f"{count} faces")
        return {'FINISHED'}


class UV_OT_maya_isolate(UVOperator, bpy.types.Operator):
    """Show only the selected UVs (Maya: Isolate Select). Press again to show everything"""
    bl_idname = "uv.maya_isolate"
    bl_label = "Isolate Select"

    def execute(self, context):
        s = settings(context)
        if s.isolate:
            bpy.ops.uv.reveal(select=False)
            s.isolate = False
        else:
            bpy.ops.uv.hide(unselected=True)
            s.isolate = True
        return {'FINISHED'}


class UV_OT_maya_unpin_all(UVOperator, bpy.types.Operator):
    """Remove every pin of the visible UVs"""
    bl_idname = "uv.maya_unpin_all"
    bl_label = "Unpin All"

    def execute(self, context):
        for ob, bm, uv, faces in edit_meshes(context):
            for f in faces:
                for l in f.loops:
                    l[uv].pin_uv = False
            update(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Shift + double-click shortest path
# ---------------------------------------------------------------------------

# The UV active element is replaced by every click (its history is cleared),
# so remember the previous one: mesh pointer -> [previous, current] snapshots.
_uv_active = {}


def _history_snapshot(bm):
    return tuple((type(e).__name__, e.index) for e in bm.select_history)


@persistent
def track_uv_active(scene, depsgraph=None):
    view_layer = bpy.context.view_layer
    ob = view_layer.objects.active if view_layer else None
    if ob is None or ob.type != 'MESH' or ob.mode != 'EDIT':
        return
    bm = bmesh.from_edit_mesh(ob.data)
    snap = _history_snapshot(bm)
    hist = _uv_active.setdefault(ob.data.as_pointer(), [(), ()])
    if snap and snap != hist[1]:
        hist[0], hist[1] = hist[1], snap


def restore_snapshot(bm, snap):
    seqs = {"BMVert": bm.verts, "BMEdge": bm.edges, "BMFace": bm.faces}
    for seq in seqs.values():
        seq.ensure_lookup_table()
    elems = []
    for kind, index in snap:
        seq = seqs.get(kind)
        if seq is None or index >= len(seq):
            return False
        elems.append(seq[index])
    bm.select_history.clear()
    for e in elems:
        bm.select_history.add(e)
    return True


class UV_OT_maya_path_select(bpy.types.Operator):
    """Select the shortest UV path from the previously selected element to the clicked one"""
    bl_idname = "uv.maya_path_select"
    bl_label = "Shift + Double-Click Path Select (Maya)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.mode == 'EDIT_MESH' and context.area is not None
                and context.area.type == 'IMAGE_EDITOR')

    def invoke(self, context, event):
        ob = context.edit_object
        prev = _uv_active.get(ob.data.as_pointer(), [(), ()])[0]
        ops = context.window_manager.operators
        just_toggled = bool(ops) and ops[-1].bl_idname == "UV_OT_select" and \
            getattr(ops[-1].properties, "toggle", False)
        if just_toggled and prev:
            bm = bmesh.from_edit_mesh(ob.data)
            if restore_snapshot(bm, prev):
                update(ob)
        result = bpy.ops.uv.shortest_path_pick('INVOKE_DEFAULT')
        return {'FINISHED'} if 'FINISHED' in result else {'PASS_THROUGH'}


def clear_state():
    _uv_active.clear()


classes = (
    UV_OT_maya_component_mode,
    UV_OT_maya_convert_selection,
    UV_OT_maya_select_by_type,
    UV_OT_maya_isolate,
    UV_OT_maya_unpin_all,
    UV_OT_maya_path_select,
)
