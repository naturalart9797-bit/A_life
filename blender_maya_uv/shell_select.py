# UV Shell selection in the 3D Viewport (Maya: UV Shell component mode).
#
# While enabled, every selection change in mesh edit mode is grown to whole
# UV shells: selecting any face of a shell selects the shell, deselecting a
# face of a shell deselects the shell. Works with click, box, lasso, etc.

import bpy
import bmesh
from bpy.app.handlers import persistent

from .common import islands


# mesh pointer -> frozenset of selected face indices after the last update
_prev = {}
_state = {"busy": False, "last_op": 0}


def _update_shell_select(self, context):
    ts = context.scene.tool_settings
    if self.shell_select_3d:
        self.shell_prev_select_mode = ",".join(str(int(b)) for b in ts.mesh_select_mode)
        ts.mesh_select_mode = (False, False, True)
    elif self.shell_prev_select_mode:
        ts.mesh_select_mode = tuple(c == "1" for c in self.shell_prev_select_mode.split(","))
    _prev.clear()
    from . import mode_watch
    mode_watch.remember(context)


def _new_replace_click():
    """True when a click-select that replaces the selection just ran.

    "Click a face of an already selected shell" removes faces without adding
    any, exactly like Shift-click deselect, so tell them apart by the operator.
    """
    wm = bpy.context.window_manager
    if wm is None or not wm.operators:
        return False
    op = wm.operators[-1]
    ptr = op.as_pointer()
    if ptr == _state["last_op"]:
        return False
    _state["last_op"] = ptr
    if op.bl_idname != "VIEW3D_OT_select":
        return False
    p = op.properties
    return not (getattr(p, "extend", False) or getattr(p, "deselect", False)
                or getattr(p, "toggle", False))


def grow_to_shells(ob, replace=False):
    """Grow / shrink the face selection of ob to whole UV shells. Returns True if changed."""
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    uv = bm.loops.layers.uv.active
    if uv is None:
        return False
    bm.faces.ensure_lookup_table()
    current = frozenset(f.index for f in bm.faces if f.select)
    key = me.as_pointer()
    prev = _prev.get(key)
    if prev is None or (prev and max(prev) >= len(bm.faces)):
        prev = frozenset()
    added = current - prev
    removed = prev - current
    if not added and not removed and not replace:
        _prev[key] = current
        return False

    faces = [f for f in bm.faces if not f.hide]
    shell_of = {}
    shells = islands(faces, uv)
    for i, isl in enumerate(shells):
        for f in isl:
            shell_of[f.index] = i

    if added or replace:
        # Selecting: every shell that has a selected face becomes fully selected.
        keep = {shell_of[i] for i in current if i in shell_of}
        final = {f.index for i in keep for f in shells[i]}
    else:
        # Only deselecting: drop the whole shell of every deselected face.
        drop = {shell_of[i] for i in removed if i in shell_of}
        final = {i for i in current if shell_of.get(i) not in drop}

    changed = False
    if final != current:
        for i in current - final:
            bm.faces[i].select_set(False)
        for i in final - current:
            bm.faces[i].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
        changed = True
    _prev[key] = frozenset(final)
    return changed


@persistent
def on_depsgraph_update(scene, depsgraph=None):
    if _state["busy"]:
        return
    s = getattr(scene, "maya_uv", None)
    if s is None or not s.shell_select_3d:
        return
    view_layer = bpy.context.view_layer
    if view_layer is None:
        return
    objects = [ob for ob in view_layer.objects if ob.type == 'MESH' and ob.mode == 'EDIT']
    if not objects:
        _prev.clear()
        return
    replace = _new_replace_click()
    _state["busy"] = True
    try:
        changed = False
        for ob in objects:
            changed |= grow_to_shells(ob, replace)
    finally:
        _state["busy"] = False
    if changed and bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def clear_state():
    _prev.clear()
