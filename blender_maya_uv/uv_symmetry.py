# Symmetric UV selection (Maya: UV Toolkit > Select > Symmetry).
#
# While enabled, selecting UVs / UV edges / faces also selects their mirror:
#  * X / Y / Z: the mirror across the object's local axis in 3D (topology),
#  * U / V: the mirror across a line in UV space (U = center or V = center).
# Works in the UV editor (with or without sync selection) and in the 3D view.

import bpy
import bmesh
from bpy.app.handlers import persistent
from mathutils import Vector, kdtree

from .common import (
    is_sync, visible_faces, loop_selected, loop_edge_selected, face_selected,
    set_selection, update,
)


_prev = {}   # mesh pointer -> (mode key, frozenset of selected element keys)
_cache = {}  # mesh pointer -> (signature, vert map) for 3D symmetry
_state = {"busy": False, "last_op": 0}

AXES = {'X': 0, 'Y': 1, 'Z': 2}


# ---------------------------------------------------------------------------
# Mirror lookups
# ---------------------------------------------------------------------------

def _vert_map(me, bm, axis, tol):
    sig = (len(bm.verts), len(bm.faces), axis, tol)
    key = me.as_pointer()
    cached = _cache.get(key)
    if cached and cached[0] == sig:
        return cached[1]
    bm.verts.ensure_lookup_table()
    tree = kdtree.KDTree(len(bm.verts))
    for v in bm.verts:
        tree.insert(v.co, v.index)
    tree.balance()
    i = AXES[axis]
    vmap = {}
    for v in bm.verts:
        co = v.co.copy()
        co[i] = -co[i]
        _co, idx, dist = tree.find(co)
        if idx is not None and dist <= tol:
            vmap[v.index] = idx
    _cache[key] = (sig, vmap)
    return vmap


class _Mirror:
    """Find the mirror of loops (UV vertices / edges) and faces."""

    def __init__(self, me, bm, uv, faces, settings):
        self.bm, self.uv, self.faces = bm, uv, faces
        self.mode = settings.uv_symmetry
        self.tol = settings.uv_symmetry_tolerance
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        if self.mode in AXES:
            self.vmap = _vert_map(me, bm, self.mode, self.tol)
            self.face_of = {frozenset(v.index for v in f.verts): f for f in faces}
        else:
            self.center = settings.uv_symmetry_center
            self.loops = [l for f in faces for l in f.loops]
            self.tree = kdtree.KDTree(max(len(self.loops), 1))
            for i, l in enumerate(self.loops):
                co = l[uv].uv
                self.tree.insert((co.x, co.y, 0.0), i)
            self.tree.balance()
            self.face_tree = kdtree.KDTree(max(len(faces), 1))
            for i, f in enumerate(faces):
                c = self._face_uv_center(f)
                self.face_tree.insert((c.x, c.y, 0.0), i)
            self.face_tree.balance()

    # UV-space helpers
    def _mirror_uv(self, co):
        if self.mode == 'U':
            return Vector((2 * self.center - co.x, co.y))
        return Vector((co.x, 2 * self.center - co.y))

    def _face_uv_center(self, f):
        return sum((l[self.uv].uv for l in f.loops), Vector((0.0, 0.0))) / len(f.loops)

    def _loops_at(self, co):
        return [self.loops[i] for _c, i, _d in self.tree.find_range((co.x, co.y, 0.0), self.tol)]

    # 3D helpers
    def _mirror_face_3d(self, f):
        try:
            key = frozenset(self.vmap[v.index] for v in f.verts)
        except KeyError:
            return None
        return self.face_of.get(key)

    def face(self, f):
        """Faces at the mirrored position (every overlapping one in UV space)."""
        if self.mode in AXES:
            m = self._mirror_face_3d(f)
            return [m] if m is not None else []
        c = self._mirror_uv(self._face_uv_center(f))
        return [self.faces[i] for _co, i, _d in self.face_tree.find_range((c.x, c.y, 0.0), self.tol)]

    def vert_loops(self, l):
        """Loops at the mirrored UV vertex."""
        if self.mode in AXES:
            mf = self._mirror_face_3d(l.face)
            mv = self.vmap.get(l.vert.index)
            if mf is None or mv is None:
                return []
            return [m for m in mf.loops if m.vert.index == mv]
        return self._loops_at(self._mirror_uv(l[self.uv].uv))

    def edge_loops(self, l):
        """Loops whose UV edge is the mirror of l's UV edge."""
        if self.mode in AXES:
            mf = self._mirror_face_3d(l.face)
            a, b = self.vmap.get(l.vert.index), self.vmap.get(l.link_loop_next.vert.index)
            if mf is None or a is None or b is None:
                return []
            return [m for m in mf.loops if {m.vert.index, m.link_loop_next.vert.index} == {a, b}]
        a = self._mirror_uv(l[self.uv].uv)
        b = self._mirror_uv(l.link_loop_next[self.uv].uv)
        out = []
        for m in self._loops_at(a):
            for n in (m.link_loop_next, m.link_loop_prev):
                if (n[self.uv].uv - b).length <= self.tol:
                    out.append(m if n is m.link_loop_next else n)
        return out


# ---------------------------------------------------------------------------
# Selection diff
# ---------------------------------------------------------------------------

def _mode(context, sync):
    ts = context.scene.tool_settings
    if sync:
        return sync  # 'VERT' / 'EDGE' / 'FACE'
    if getattr(ts, "use_uv_select_island", False):
        return 'FACE'
    return {'VERTEX': 'VERT', 'EDGE': 'EDGE', 'FACE': 'FACE', 'ISLAND': 'FACE'}[ts.uv_select_mode]


def _selected_keys(faces, uv, sync, mode):
    if mode == 'FACE':
        return frozenset(f.index for f in faces if face_selected(f, uv, sync))
    test = loop_edge_selected if mode == 'EDGE' else loop_selected
    return frozenset((f.index, i) for f in faces for i, l in enumerate(f.loops) if test(l, uv, sync))


def _elements(bm, keys, mode):
    if mode == 'FACE':
        return [bm.faces[k] for k in keys]
    return [bm.faces[fi].loops[i] for fi, i in keys]


def _new_replace_click():
    wm = bpy.context.window_manager
    if wm is None or not wm.operators:
        return False
    op = wm.operators[-1]
    ptr = op.as_pointer()
    if ptr == _state["last_op"]:
        return False
    _state["last_op"] = ptr
    if op.bl_idname not in {"UV_OT_select", "VIEW3D_OT_select"}:
        return False
    p = op.properties
    return not (getattr(p, "extend", False) or getattr(p, "deselect", False)
                or getattr(p, "toggle", False))


def symmetrize(context, ob, settings, replace=False):
    """Mirror the selection change of ob since the last call. Returns True if changed."""
    me = ob.data
    bm = bmesh.from_edit_mesh(me)
    uv = bm.loops.layers.uv.active
    if uv is None:
        return False
    sync = is_sync(context)
    mode = _mode(context, sync)
    faces = visible_faces(bm, sync)
    bm.faces.ensure_lookup_table()
    current = _selected_keys(faces, uv, sync, mode)
    key = me.as_pointer()
    prev = _prev.get(key)
    if prev is None or prev[0] != (mode, len(bm.faces)) or replace:
        added, removed = current, frozenset()
    else:
        added, removed = current - prev[1], prev[1] - current
    if not added and not removed:
        _prev[key] = ((mode, len(bm.faces)), current)
        return False

    mirror = _Mirror(me, bm, uv, faces, settings)
    find = {'FACE': mirror.face,
            'EDGE': mirror.edge_loops,
            'VERT': mirror.vert_loops}[mode]
    to_select, to_deselect = [], []
    for e in _elements(bm, added, mode):
        to_select.extend(find(e))
    for e in _elements(bm, removed, mode):
        to_deselect.extend(find(e))
    added_elems = set(_elements(bm, added, mode))
    to_deselect = [m for m in to_deselect if m not in added_elems and m not in set(to_select)]

    def select(elems, state):
        if not elems:
            return
        kw = {'FACE': "select_faces", 'EDGE': "edges", 'VERT': "verts"}[mode]
        set_selection(context, bm, uv, faces, state=state, **{kw: elems})

    select(to_deselect, False)
    select(to_select, True)
    after = _selected_keys(faces, uv, sync, mode)
    changed = after != current
    if changed:
        update(ob)
    _prev[key] = ((mode, len(bm.faces)), after)
    return changed


@persistent
def on_depsgraph_update(scene, depsgraph=None):
    if _state["busy"]:
        return
    s = getattr(scene, "maya_uv", None)
    if s is None or s.uv_symmetry == 'OFF':
        return
    context = bpy.context
    view_layer = context.view_layer
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
            changed |= symmetrize(context, ob, s, replace)
    finally:
        _state["busy"] = False
    if changed and context.screen:
        for area in context.screen.areas:
            if area.type in {'IMAGE_EDITOR', 'VIEW_3D'}:
                area.tag_redraw()


def reset(self=None, context=None):
    _prev.clear()
    _cache.clear()


clear_state = reset
