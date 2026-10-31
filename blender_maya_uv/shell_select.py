# UV Shell selection in the 3D Viewport (Maya: UV Shell component mode).
#
# Linked to the UV Editor's island (shell) select: while it is on, every
# selection made in the 3D Viewport is grown to whole UV shells, like the UV
# Editor does. Selecting any part of a shell selects the shell, deselecting a
# part of it deselects the shell. Works with click, box, lasso, etc., and in
# vertex, edge and face mode.

import bpy
import bmesh
from bpy.app.handlers import persistent

from .common import islands


# mesh pointer -> (mode, frozenset of selected element indices) after the last update
_prev = {}
_state = {"busy": False, "last_op": 0}


def is_active(ts):
    """Shell select is on (Blender 5: Island toggle, Blender 4: Island mode)."""
    return getattr(ts, "use_uv_select_island", False) or ts.uv_select_mode == 'ISLAND'


def set_active(ts, value):
    _prev.clear()
    if hasattr(ts, "use_uv_select_island"):
        ts.use_uv_select_island = value
    elif value:
        ts.uv_select_mode = 'ISLAND'
    elif ts.uv_select_mode == 'ISLAND':
        ts.uv_select_mode = 'FACE'
    from . import mode_watch
    mode_watch.remember(bpy.context)


def _new_operator():
    """(idname, properties) of an operator that ran since the last call, else None."""
    wm = bpy.context.window_manager
    if wm is None or not wm.operators:
        return None
    op = wm.operators[-1]
    ptr = op.as_pointer()
    if ptr == _state["last_op"]:
        return None
    _state["last_op"] = ptr
    return op.bl_idname, op.properties


def _is_replace_click(op):
    """A click-select that replaces the selection.

    "Click a face of an already selected shell" removes faces without adding
    any, exactly like Shift-click deselect, so tell them apart by the operator.
    """
    if op is None or op[0] != "VIEW3D_OT_select":
        return False
    p = op[1]
    return not (getattr(p, "extend", False) or getattr(p, "deselect", False)
                or getattr(p, "toggle", False))


def _mesh_mode(ts):
    vert, edge, _face = ts.mesh_select_mode
    return 'VERT' if vert else ('EDGE' if edge else 'FACE')


def sync_uv_flags(bm, ts):
    """With UV sync select, Blender 5 keeps its own UV selection: refresh it from the
    mesh selection, or the UV editor would move only part of the selection."""
    if ts.use_uv_select_sync and getattr(bm, "uv_select_sync_valid", False):
        bm.uv_select_sync_from_mesh()


def _elements(bm, mode):
    elems = {'VERT': bm.verts, 'EDGE': bm.edges, 'FACE': bm.faces}[mode]
    elems.ensure_lookup_table()
    return elems


def remember(ob, ts):
    """Take the current selection as the baseline (no growing)."""
    bm = bmesh.from_edit_mesh(ob.data)
    mode = _mesh_mode(ts)
    elems = _elements(bm, mode)
    _prev[ob.data.as_pointer()] = ((mode, len(elems)), frozenset(e.index for e in elems if e.select))


def grow_to_shells(ob, ts, replace=False):
    """Grow / shrink the selection of ob to whole UV shells. Returns True if changed."""
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    uv = bm.loops.layers.uv.active
    if uv is None:
        return False
    mode = _mesh_mode(ts)
    elems = _elements(bm, mode)
    current = frozenset(e.index for e in elems if e.select)
    key = me.as_pointer()
    prev = _prev.get(key)
    if prev is None or prev[0] != (mode, len(elems)):
        # First look (or the mode changed): take the selection as it is.
        _prev[key] = ((mode, len(elems)), current)
        return False
    added, removed = current - prev[1], prev[1] - current
    if not added and not removed and not replace:
        return False

    faces = [f for f in bm.faces if not f.hide]
    shells = islands(faces, uv)
    shell_of = {f: i for i, isl in enumerate(shells) for f in isl}

    def shells_of(indices, prefer_selected=False):
        out = set()
        for i in indices:
            e = elems[i]
            fs = [f for f in ([e] if mode == 'FACE' else e.link_faces) if f in shell_of]
            if prefer_selected:
                # A seam vertex / edge also touches the neighboring shell: count only
                # the selected faces around it when there are any.
                fs = [f for f in fs if f.select] or fs
            out.update(shell_of[f] for f in fs)
        return out

    # Shells already selected: those with a selected face.
    selected = {shell_of[f] for f in faces if f.select}
    if replace:
        keep = shells_of(current, prefer_selected=True)
    elif added:
        keep = selected | shells_of(added, prefer_selected=True)
    else:
        keep = selected - shells_of(removed)

    final_faces = {f for i in keep for f in shells[i]}
    final_verts = {v for f in final_faces for v in f.verts}
    final_edges = {e for f in final_faces for e in f.edges}
    changed = False
    # Faces first: deselecting a face also deselects its vertices, which would
    # drop the seam vertices the neighboring selected shell still uses.
    for seq, final in ((bm.faces, final_faces), (bm.edges, final_edges), (bm.verts, final_verts)):
        for e in seq:
            if e.hide:
                continue
            want = e in final
            if e.select != want:
                e.select = want
                changed = True
    if changed:
        bm.select_flush_mode()
        sync_uv_flags(bm, ts)
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
    _prev[key] = ((mode, len(elems)), frozenset(e.index for e in elems if e.select))
    return changed


@persistent
def on_depsgraph_update(scene, depsgraph=None):
    if _state["busy"]:
        return
    ts = scene.tool_settings
    op = _new_operator()
    if not is_active(ts):
        _prev.clear()
        return
    view_layer = bpy.context.view_layer
    if view_layer is None:
        return
    objects = [ob for ob in view_layer.objects if ob.type == 'MESH' and ob.mode == 'EDIT']
    if not objects:
        _prev.clear()
        return
    if op is not None and op[0].startswith("UV_OT_"):
        # Selected in the UV editor: Blender already selects whole islands there.
        for ob in objects:
            remember(ob, ts)
        return
    _state["busy"] = True
    try:
        changed = False
        for ob in objects:
            changed |= grow_to_shells(ob, ts, _is_replace_click(op))
    finally:
        _state["busy"] = False
    if changed and bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type in {'VIEW_3D', 'IMAGE_EDITOR'}:
                area.tag_redraw()


def clear_state():
    _prev.clear()
