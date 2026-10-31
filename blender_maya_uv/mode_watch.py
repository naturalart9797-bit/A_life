# Maya-style component switching.
#
# Blender keeps UV island (shell) select on when you pick vertex / edge / face,
# and converts the selection to the new mode. Maya leaves shell mode and starts
# the new component type with nothing selected. This watches the select modes
# (header buttons, number keys, marking menus) and:
#  * leaves shell mode (UV editor island select, 3D UV Shell Select) when
#    another component mode is picked,
#  * clears the selection when leaving shell mode, and (preference) whenever
#    the UV component mode changes without sync selection.

import bpy
import bmesh
from bpy.app.handlers import persistent

from . import shell_select
from .common import addon_prefs, edit_objects


_state = {"snap": None, "busy": False}
_owner = object()


def _snapshot(ts):
    return (ts.uv_select_mode, getattr(ts, "use_uv_select_island", False),
            tuple(ts.mesh_select_mode), ts.use_uv_select_sync)


def remember(context):
    """Accept the current modes as-is (used by tools that switch modes on purpose)."""
    _state["snap"] = _snapshot(context.scene.tool_settings)


def clear_selection(context, mesh):
    """Deselect everything: UV selection, or the mesh selection with mesh=True."""
    new_api = hasattr(bmesh.types.BMLoop, "uv_select_vert_set")
    for ob in edit_objects(context):
        bm = bmesh.from_edit_mesh(ob.data)
        if mesh:
            for seq in (bm.faces, bm.edges, bm.verts):
                for e in seq:
                    e.select = False
            bm.select_history.clear()
            shell_select.sync_uv_flags(bm, context.scene.tool_settings)
        else:
            uv = bm.loops.layers.uv.active
            if uv is None:
                continue
            for f in bm.faces:
                if new_api:
                    f.uv_select_set(False)
                for l in f.loops:
                    if new_api:
                        l.uv_select_vert_set(False)
                        l.uv_select_edge_set(False)
                    else:
                        l[uv].select = False
                        l[uv].select_edge = False
        bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)


def check(context):
    if _state["busy"] or context.mode != 'EDIT_MESH':
        return
    scene = context.scene
    s = getattr(scene, "maya_uv", None)
    if s is None:
        return
    ts = scene.tool_settings
    prev, cur = _state["snap"], _snapshot(ts)
    _state["snap"] = cur
    if prev is None or prev == cur or prev[3] != cur[3]:
        return  # first look, nothing changed, or sync selection was toggled

    prefs = addon_prefs(context)
    clear = prefs is None or prefs.clear_on_mode_change
    sync = cur[3]
    _state["busy"] = True
    try:
        component_changed = prev[2] != cur[2] if sync else prev[0] != cur[0]
        left_shell = prev[1] and not cur[1]
        if component_changed and prev[1] and cur[1]:
            # Picked another component while in shell mode: leave shell mode.
            ts.use_uv_select_island = False
            left_shell = True
        elif not sync and prev[1] and cur[1] and prev[2] != cur[2]:
            # Picked a component in the 3D view: leave shell mode there too. The 3D
            # selection decides what the UV editor shows, so keep it.
            ts.use_uv_select_island = False
            return
        if clear and (left_shell or (component_changed and not sync)):
            clear_selection(context, mesh=sync)
    finally:
        _state["snap"] = _snapshot(ts)
        _state["busy"] = False


@persistent
def on_depsgraph_update(scene, depsgraph=None):
    # uv.select_mode / mesh.select_mode don't publish RNA changes, but they tag
    # the depsgraph, so this catches the header buttons and number keys.
    check(bpy.context)


def _on_msgbus():
    check(bpy.context)


def subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    for prop in ("uv_select_mode", "use_uv_select_island", "mesh_select_mode"):
        if prop in bpy.types.ToolSettings.bl_rna.properties:
            bpy.msgbus.subscribe_rna(key=(bpy.types.ToolSettings, prop), owner=_owner,
                                     args=(), notify=_on_msgbus)


@persistent
def on_load_post(*_args):
    _state["snap"] = None
    subscribe()


def unsubscribe():
    bpy.msgbus.clear_by_owner(_owner)
    _state["snap"] = None
