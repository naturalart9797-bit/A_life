# Maya-style selection helpers for Blender's mesh edit mode.
#
#  * Symmetry selection: selecting on one side also selects the mirrored
#    vertex / edge / face (works with click, box, circle, lasso, loop, path...).
#  * Path click select: plain left-click picks the shortest path from the
#    last selected element (like Maya's "Select Shortest Edge Path Tool").
#  * Double-click loop select: double-click an edge to select its loop,
#    Shift + double-click to add a loop.

bl_info = {
    "name": "Maya Style Select",
    "author": "naturalart9797",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "View3D > Sidebar (N) > Maya",
    "description": "Maya-like symmetry selection, path click select and double-click loop select",
    "category": "Mesh",
}

import bpy
import bmesh
from bpy.app.handlers import persistent
from mathutils import kdtree


AXIS_INDEX = {'X': 0, 'Y': 1, 'Z': 2}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _update_symmetry(self, context):
    # Also turn on Blender's own mesh mirror so transforms are symmetric too,
    # which is what Maya's symmetry does.
    for ob in _edit_mesh_objects(context.view_layer):
        _apply_mesh_mirror(ob.data, self)
    _cache.clear()
    _prev_sel.clear()


class MayaSelectSettings(bpy.types.PropertyGroup):
    symmetry: bpy.props.BoolProperty(
        name="Symmetry",
        description="Mirror selection (and transforms) across the chosen local axis",
        default=False,
        update=_update_symmetry,
    )
    symmetry_axis: bpy.props.EnumProperty(
        name="Axis",
        items=[('X', "X", "Object X axis"), ('Y', "Y", "Object Y axis"), ('Z', "Z", "Object Z axis")],
        default='X',
        update=_update_symmetry,
    )
    tolerance: bpy.props.FloatProperty(
        name="Tolerance",
        description="Max distance for a vertex to count as the mirror of another",
        default=0.001, min=0.0, soft_max=0.1, precision=4,
        update=_update_symmetry,
    )
    mirror_transform: bpy.props.BoolProperty(
        name="Mirror Transform",
        description="Also enable Blender's mesh mirror option so moving one side moves the other",
        default=True,
        update=_update_symmetry,
    )
    path_click: bpy.props.BoolProperty(
        name="Path Click Select",
        description="Left-click selects the shortest path from the last selected element. "
                    "Click empty space to clear and start a new path",
        default=False,
    )
    double_click_loop: bpy.props.BoolProperty(
        name="Double-Click Loop",
        description="Double-click selects an edge loop, Shift + double-click adds a loop",
        default=True,
    )


def _settings(context=None):
    scene = (context or bpy.context).scene
    return getattr(scene, "maya_select", None) if scene else None


def _apply_mesh_mirror(me, settings):
    on = settings.symmetry and settings.mirror_transform
    axis = settings.symmetry_axis
    me.use_mirror_x = on and axis == 'X'
    me.use_mirror_y = on and axis == 'Y'
    me.use_mirror_z = on and axis == 'Z'


def _edit_mesh_objects(view_layer):
    if view_layer is None:
        return []
    return [ob for ob in view_layer.objects if ob.type == 'MESH' and ob.mode == 'EDIT']


# ---------------------------------------------------------------------------
# Mirror maps (vertex / edge / face index -> mirrored index)
# ---------------------------------------------------------------------------

# key: mesh pointer -> (signature, vmap, emap, fmap)
_cache = {}
# key: mesh pointer -> (mode, frozenset of selected indices)
_prev_sel = {}
_state = {"busy": False, "last_op": 0}


def _build_maps(bm, axis, tol):
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    tree = kdtree.KDTree(len(bm.verts))
    for v in bm.verts:
        tree.insert(v.co, v.index)
    tree.balance()

    vmap = {}
    for v in bm.verts:
        co = v.co.copy()
        co[axis] = -co[axis]
        hit = tree.find(co)
        if hit[1] is not None and hit[2] <= tol:
            vmap[v.index] = hit[1]

    edge_lookup = {frozenset(v.index for v in e.verts): e.index for e in bm.edges}
    emap = {}
    for key, idx in edge_lookup.items():
        mk = frozenset(vmap.get(i, -1) for i in key)
        m = edge_lookup.get(mk)
        if m is not None:
            emap[idx] = m

    face_lookup = {frozenset(v.index for v in f.verts): f.index for f in bm.faces}
    fmap = {}
    for key, idx in face_lookup.items():
        mk = frozenset(vmap.get(i, -1) for i in key)
        m = face_lookup.get(mk)
        if m is not None:
            fmap[idx] = m

    return vmap, emap, fmap


def _get_maps(me, bm, settings):
    sig = (len(bm.verts), len(bm.edges), len(bm.faces), settings.symmetry_axis, settings.tolerance)
    key = me.as_pointer()
    cached = _cache.get(key)
    if cached is None or cached[0] != sig:
        maps = _build_maps(bm, AXIS_INDEX[settings.symmetry_axis], settings.tolerance)
        cached = (sig,) + maps
        _cache[key] = cached
    return cached[1:]


def _primary_mode(tool_settings):
    vert, edge, _face = tool_settings.mesh_select_mode
    return 'VERT' if vert else ('EDGE' if edge else 'FACE')


def _elements(bm, mode):
    return bm.verts if mode == 'VERT' else (bm.edges if mode == 'EDGE' else bm.faces)


# ---------------------------------------------------------------------------
# Symmetry: keep selection mirrored
# ---------------------------------------------------------------------------

def _last_op_is_replace_select():
    """True when a new click-select (without extend/deselect/toggle) just ran.

    "Click an already selected element" and "Ctrl/Shift deselect its mirror"
    produce the same selection diff, so look at the operator to tell them apart.
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


def symmetrize_selection(ob, settings, mode, replace=False):
    """Mirror the selection change since the last call. Returns True if changed."""
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    vmap, emap, fmap = _get_maps(me, bm, settings)
    mirror = vmap if mode == 'VERT' else (emap if mode == 'EDGE' else fmap)

    elems = _elements(bm, mode)
    elems.ensure_lookup_table()
    current = frozenset(e.index for e in elems if e.select)

    key = me.as_pointer()
    prev = _prev_sel.get(key)
    if prev is None or prev[0] != mode or prev[2] != len(elems):
        # First look at this mesh (or mode/topology changed): just mirror everything.
        added, removed = current, frozenset()
    elif replace:
        added, removed = current, frozenset()
    else:
        added = current - prev[1]
        removed = prev[1] - current

    to_select = {mirror[i] for i in added if i in mirror}
    to_deselect = {mirror[i] for i in removed if i in mirror} - added - to_select
    to_select -= current

    changed = False
    if to_select or to_deselect:
        for i in to_deselect:
            elems[i].select_set(False)
        for i in to_select:
            elems[i].select_set(True)
        bm.select_flush_mode()
        bmesh.update_edit_mesh(me, loop_triangles=False, destructive=False)
        changed = True
        current = frozenset(e.index for e in elems if e.select)

    _prev_sel[key] = (mode, current, len(elems))
    return changed


@persistent
def _on_depsgraph_update(scene, depsgraph=None):
    if _state["busy"]:
        return
    settings = getattr(scene, "maya_select", None)
    if settings is None or not settings.symmetry:
        return
    view_layer = bpy.context.view_layer
    objects = _edit_mesh_objects(view_layer)
    if not objects:
        _prev_sel.clear()
        return

    mode = _primary_mode(scene.tool_settings)
    replace = _last_op_is_replace_select()
    _state["busy"] = True
    try:
        changed = False
        for ob in objects:
            changed |= symmetrize_selection(ob, settings, mode, replace)
    finally:
        _state["busy"] = False

    if changed and bpy.context.screen:
        for area in bpy.context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


@persistent
def _on_load_post(*_args):
    _cache.clear()
    _prev_sel.clear()


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class MESH_OT_maya_path_click_select(bpy.types.Operator):
    """Select the shortest path from the last selected element to the clicked one"""
    bl_idname = "mesh.maya_path_click_select"
    bl_label = "Path Click Select (Maya)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        # Failing poll lets the click fall through to normal selection.
        return (s is not None and s.path_click and context.mode == 'EDIT_MESH'
                and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        # shortest_path_pick selects the clicked element when nothing is selected,
        # and returns PASS_THROUGH when nothing is under the cursor (or there is no
        # path). Passing through lets the normal click-select run, so clicking
        # empty space clears the selection and box-drag from empty space still works.
        result = bpy.ops.mesh.shortest_path_pick('INVOKE_DEFAULT')
        if 'FINISHED' in result:
            return {'FINISHED'}
        return {'PASS_THROUGH'}


class MESH_OT_maya_loop_select(bpy.types.Operator):
    """Select the edge loop under the cursor (Maya-style double-click)"""
    bl_idname = "mesh.maya_loop_select"
    bl_label = "Double-Click Loop Select (Maya)"
    bl_options = {'REGISTER', 'UNDO'}

    extend: bpy.props.BoolProperty(name="Extend", default=False)

    @classmethod
    def poll(cls, context):
        s = _settings(context)
        return (s is not None and s.double_click_loop and context.mode == 'EDIT_MESH'
                and context.area is not None and context.area.type == 'VIEW_3D')

    def invoke(self, context, event):
        return bpy.ops.mesh.loop_select('INVOKE_DEFAULT', extend=self.extend, deselect=False,
                                        toggle=False, ring=False)


class MESH_OT_maya_symmetrize_selection(bpy.types.Operator):
    """Add the mirror of the current selection"""
    bl_idname = "mesh.maya_symmetrize_selection"
    bl_label = "Mirror Current Selection"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def execute(self, context):
        settings = _settings(context)
        mode = _primary_mode(context.tool_settings)
        _cache.clear()
        for ob in _edit_mesh_objects(context.view_layer):
            symmetrize_selection(ob, settings, mode, replace=True)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class VIEW3D_PT_maya_select(bpy.types.Panel):
    bl_label = "Maya Select"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Maya"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout

        box = layout.box()
        box.prop(s, "symmetry", toggle=True, icon='MOD_MIRROR')
        col = box.column()
        col.enabled = s.symmetry
        col.row().prop(s, "symmetry_axis", expand=True)
        col.prop(s, "tolerance")
        col.prop(s, "mirror_transform")
        col.operator(MESH_OT_maya_symmetrize_selection.bl_idname, icon='ARROW_LEFTRIGHT')

        box = layout.box()
        box.prop(s, "path_click", toggle=True, icon='TRACKING')
        box.prop(s, "double_click_loop")


# ---------------------------------------------------------------------------
# Keymap
# ---------------------------------------------------------------------------

_keymaps = []


# Active-tool keymaps are handled before the "Mesh" keymap and select on PRESS,
# so the path pick has to sit in them (addon items are prepended, so ours wins).
# Circle select is left out on purpose: its press starts painting.
_PATH_CLICK_KEYMAPS = (
    ("Mesh", 'EMPTY'),
    ("3D View Tool: Tweak", 'VIEW_3D'),
    ("3D View Tool: Tweak (fallback)", 'VIEW_3D'),
    ("3D View Tool: Select Box", 'VIEW_3D'),
    ("3D View Tool: Select Box (fallback)", 'VIEW_3D'),
    ("3D View Tool: Select Lasso", 'VIEW_3D'),
    ("3D View Tool: Select Lasso (fallback)", 'VIEW_3D'),
    ("3D View Tool: Select Circle (fallback)", 'VIEW_3D'),
)


def _select_mouse():
    prefs = getattr(bpy.context.window_manager.keyconfigs.active, "preferences", None)
    return 'RIGHTMOUSE' if getattr(prefs, "select_mouse", 'LEFT') == 'RIGHT' else 'LEFTMOUSE'


def _register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    select_mouse = _select_mouse()
    for name, space_type in _PATH_CLICK_KEYMAPS:
        km = kc.keymaps.new(name=name, space_type=space_type, region_type='WINDOW')
        kmi = km.keymap_items.new(MESH_OT_maya_path_click_select.bl_idname, select_mouse, 'PRESS')
        _keymaps.append((km, kmi))

    km = kc.keymaps.new(name="Mesh", space_type='EMPTY')
    kmi = km.keymap_items.new(MESH_OT_maya_loop_select.bl_idname, select_mouse, 'DOUBLE_CLICK')
    kmi.properties.extend = False
    _keymaps.append((km, kmi))
    kmi = km.keymap_items.new(MESH_OT_maya_loop_select.bl_idname, select_mouse, 'DOUBLE_CLICK', shift=True)
    kmi.properties.extend = True
    _keymaps.append((km, kmi))


def _unregister_keymaps():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()


# ---------------------------------------------------------------------------

classes = (
    MayaSelectSettings,
    MESH_OT_maya_path_click_select,
    MESH_OT_maya_loop_select,
    MESH_OT_maya_symmetrize_selection,
    VIEW3D_PT_maya_select,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.maya_select = bpy.props.PointerProperty(type=MayaSelectSettings)
    bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph_update)
    bpy.app.handlers.load_post.append(_on_load_post)
    _register_keymaps()


def unregister():
    _unregister_keymaps()
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    if _on_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph_update)
    del bpy.types.Scene.maya_select
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _cache.clear()
    _prev_sel.clear()


if __name__ == "__main__":
    register()
