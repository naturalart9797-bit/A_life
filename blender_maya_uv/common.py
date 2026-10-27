# Shared settings and UV helpers.
#
# Works with both bmesh UV selection APIs: Blender 5.0+ stores UV selection on
# BMLoop / BMFace (uv_select_vert, uv_select_edge, uv_select), older versions
# on BMLoopUV (select, select_edge).

import math

import bpy
import bmesh
from mathutils import Matrix, Vector


EPS = 1e-6


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

TEXTURE_SIZES = [(str(n), str(n), "") for n in (256, 512, 1024, 2048, 4096, 8192)]


def _update_checker_image(self, context):
    from . import display
    display.update_checker_image(self)


def _update_shell_select(self, context):
    from . import shell_select
    shell_select._update_shell_select(self, context)


class MayaUVSettings(bpy.types.PropertyGroup):
    # Transform
    pivot: bpy.props.EnumProperty(
        name="Pivot",
        items=[('SELECTION', "Selection", "Center of the whole selection"),
               ('SHELL', "Shell", "Center of each shell"),
               ('ORIGIN', "UV Origin", "UV (0, 0)"),
               ('CURSOR', "2D Cursor", "UV Editor 2D cursor")],
        default='SELECTION')
    move_step: bpy.props.FloatProperty(
        name="Step", description="Distance moved by the arrow buttons (0-1 UV space)",
        default=0.1, min=0.0, soft_max=1.0, precision=3)
    rotate_angle: bpy.props.FloatProperty(
        name="Angle", description="Angle used by the rotate buttons",
        default=math.radians(90.0), subtype='ANGLE')
    scale_factor: bpy.props.FloatProperty(
        name="Scale", description="Factor used by the scale buttons",
        default=2.0, min=0.0001, soft_max=10.0)
    normalize_keep_aspect: bpy.props.BoolProperty(
        name="Keep Aspect", description="Normalize keeps the width / height ratio", default=True)
    match_tolerance: bpy.props.FloatProperty(
        name="Tolerance", description="Distance for Match UVs", default=0.01, min=0.0,
        soft_max=0.1, precision=4)
    distribute_spacing: bpy.props.FloatProperty(
        name="Spacing", description="Gap between shells for Distribute "
                                    "(0 = spread evenly between the first and last shell)",
        default=0.0, min=0.0, soft_max=0.5, precision=3)

    # Unfold
    unfold_method: bpy.props.EnumProperty(
        name="Method",
        items=[('ANGLE_BASED', "Angle Based", ""),
               ('CONFORMAL', "Conformal", ""),
               ('MINIMUM_STRETCH', "Minimum Stretch", "")],
        default='ANGLE_BASED')
    unfold_constraint: bpy.props.EnumProperty(
        name="Constraint",
        items=[('NONE', "U and V", "Unfold freely"),
               ('U', "U Only", "Unfold only along U (V stays)"),
               ('V', "V Only", "Unfold only along V (U stays)")],
        default='NONE')
    optimize_iterations: bpy.props.IntProperty(name="Iterations", default=100, min=1, soft_max=1000)
    auto_seam_angle: bpy.props.FloatProperty(
        name="Angle", description="Edges sharper than this become seams",
        default=math.radians(60.0), min=0.0, max=math.pi, subtype='ANGLE')

    # Layout
    layout_margin: bpy.props.FloatProperty(name="Shell Padding", default=0.005, min=0.0, max=1.0,
                                           precision=4)
    layout_rotate: bpy.props.BoolProperty(name="Rotate", default=True)
    layout_scale: bpy.props.BoolProperty(name="Scale to Fit", default=True)
    layout_shape: bpy.props.EnumProperty(
        name="Shape",
        items=[('CONCAVE', "Exact", "Pack using the exact shell outline"),
               ('CONVEX', "Convex", "Pack using the convex hull"),
               ('AABB', "Bounding Box", "Pack using bounding boxes (fastest)")],
        default='CONCAVE')

    # Measure / texel density
    texture_size_preset: bpy.props.EnumProperty(
        name="Map Size", items=TEXTURE_SIZES, default='2048',
        update=lambda self, context: setattr(self, "texture_size", int(self.texture_size_preset)))
    texture_size: bpy.props.IntProperty(
        name="Map Size", description="Texture resolution used for texel density",
        default=2048, min=1, soft_max=8192)
    texel_density: bpy.props.FloatProperty(
        name="Density", description="Texel density in pixels per Blender unit (meter)",
        default=1024.0, min=0.0, precision=2)
    measure_text: bpy.props.StringProperty(default="")

    # Display
    checker_type: bpy.props.EnumProperty(
        name="Checker",
        items=[('UV_GRID', "Checker", "Black and white checker"),
               ('COLOR_GRID', "Color Grid", "Colored grid with labels")],
        default='UV_GRID', update=_update_checker_image)
    checker_tiles: bpy.props.IntProperty(
        name="Resolution", description="Checker image resolution",
        default=1024, min=64, max=8192, update=_update_checker_image)
    checker_on: bpy.props.BoolProperty(default=False)
    checker_prev_shading: bpy.props.StringProperty(default="")
    show_texture_borders: bpy.props.BoolProperty(
        name="Texture Borders", description="Draw shell borders thick (Maya: Toggle texture borders)",
        default=False)
    border_width: bpy.props.FloatProperty(name="Border Width", default=3.0, min=1.0, max=10.0)
    isolate: bpy.props.BoolProperty(default=False)

    # 3D Viewport
    shell_select_3d: bpy.props.BoolProperty(
        name="UV Shell Select",
        description="Select whole UV shells in the 3D Viewport (Maya: UV Shell component). "
                    "Switches to face select while on",
        default=False, update=_update_shell_select)
    shell_prev_select_mode: bpy.props.StringProperty(default="")


def settings(context):
    return context.scene.maya_uv


def addon_prefs(context):
    addon = context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


# ---------------------------------------------------------------------------
# Reading UVs and selection
# ---------------------------------------------------------------------------

def is_sync(context):
    return context.scene.tool_settings.use_uv_select_sync


def visible_faces(bm, sync):
    if sync:
        return [f for f in bm.faces if not f.hide]
    return [f for f in bm.faces if f.select and not f.hide]


def loop_selected(loop, uv, sync):
    if sync:
        return loop.vert.select
    if hasattr(loop, "uv_select_vert"):  # Blender 5.0+
        return loop.uv_select_vert
    return loop[uv].select


def loop_edge_selected(loop, uv, sync):
    if sync:
        return loop.edge.select
    if hasattr(loop, "uv_select_edge"):
        return loop.uv_select_edge
    return loop[uv].select_edge


def face_selected(face, uv, sync):
    if sync:
        return face.select
    return all(loop_selected(l, uv, sync) for l in face.loops)


def edit_meshes(context):
    """[(object, bmesh, uv_layer, visible_faces)] for every mesh in edit mode."""
    sync = is_sync(context)
    out = []
    for ob in edit_objects(context):
        bm = bmesh.from_edit_mesh(ob.data)
        uv = bm.loops.layers.uv.active
        if uv is None:
            continue
        out.append((ob, bm, uv, visible_faces(bm, sync)))
    return out


def edit_objects(context):
    objects = getattr(context, "objects_in_mode_unique_data", None)
    if not objects:
        objects = [context.edit_object] if context.edit_object else []
    return [ob for ob in objects if ob.type == 'MESH']


def update(ob):
    bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)


def update_all(meshes):
    for ob, *_ in meshes:
        update(ob)


def selected_loops(faces, uv, sync):
    return [l for f in faces for l in f.loops if loop_selected(l, uv, sync)]


def same_uv(a, b):
    return (a - b).length_squared < EPS


def islands(faces, uv):
    """Group faces into UV shells (faces sharing an edge with matching UVs)."""
    face_set = set(faces)
    parent = {f: f for f in faces}

    def find(f):
        while parent[f] is not f:
            parent[f] = parent[parent[f]]
            f = parent[f]
        return f

    for f in faces:
        for l in f.loops:
            l2 = l.link_loop_radial_next
            while l2 is not l:
                if l2.face in face_set and uv_edge_matches(l, l2, uv):
                    ra, rb = find(f), find(l2.face)
                    if ra is not rb:
                        parent[ra] = rb
                l2 = l2.link_loop_radial_next

    groups = {}
    for f in faces:
        groups.setdefault(find(f), []).append(f)
    return list(groups.values())


def uv_edge_matches(l, l2, uv):
    """True when two loops of the same mesh edge also share that edge in UV space."""
    a1, b1 = l[uv].uv, l.link_loop_next[uv].uv
    if l2.vert is l.vert:
        a2, b2 = l2[uv].uv, l2.link_loop_next[uv].uv
    else:
        a2, b2 = l2.link_loop_next[uv].uv, l2[uv].uv
    return same_uv(a1, a2) and same_uv(b1, b2)


def is_border_loop(l, uv, face_set):
    """True when the loop's edge is a shell border (no UV-connected neighbor face)."""
    l2 = l.link_loop_radial_next
    while l2 is not l:
        if l2.face in face_set and uv_edge_matches(l, l2, uv):
            return False
        l2 = l2.link_loop_radial_next
    return True


def selected_islands(faces, uv, sync):
    return [isl for isl in islands(faces, uv)
            if any(loop_selected(l, uv, sync) for f in isl for l in f.loops)]


def island_loops(island):
    return [l for f in island for l in f.loops]


def bounds(loops, uv):
    us = [l[uv].uv.x for l in loops]
    vs = [l[uv].uv.y for l in loops]
    return Vector((min(us), min(vs))), Vector((max(us), max(vs)))


def center(loops, uv):
    lo, hi = bounds(loops, uv)
    return (lo + hi) * 0.5


def poly_area(pts, signed=False):
    a = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        a += p.x * q.y - q.x * p.y
    return a * 0.5 if signed else abs(a) * 0.5


def uv_area(f, uv, signed=False):
    return poly_area([l[uv].uv for l in f.loops], signed)


def world_area(f, mat):
    pts = [mat @ v.co for v in f.verts]
    a = Vector()
    for i in range(1, len(pts) - 1):
        a += (pts[i] - pts[0]).cross(pts[i + 1] - pts[0])
    return a.length * 0.5


def transform_loops(loops, uv, pivot, mat2):
    for l in loops:
        luv = l[uv]
        luv.uv = pivot + mat2 @ (luv.uv - pivot)


def move_loops(loops, uv, offset):
    for l in loops:
        l[uv].uv += offset


def op_available(idname):
    mod, name = idname.split(".")
    try:
        getattr(getattr(bpy.ops, mod), name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


def op_props(idname):
    mod, name = idname.split(".")
    try:
        return set(getattr(getattr(bpy.ops, mod), name).get_rna_type().properties.keys())
    except (AttributeError, KeyError):
        return set()


def call_op(idname, *args, **kwargs):
    """Call an operator, dropping keyword arguments this Blender version doesn't have."""
    props = op_props(idname)
    kwargs = {k: v for k, v in kwargs.items() if k in props}
    mod, name = idname.split(".")
    return getattr(getattr(bpy.ops, mod), name)(*args, **kwargs)


# ---------------------------------------------------------------------------
# Selection groups for transforms
# ---------------------------------------------------------------------------

def loop_groups(context, per_shell, whole_shells=False):
    """Groups of (uv_layer, loops) to transform together, and the edit meshes.

    Keep the returned meshes alive while using the loops: Blender 5 invalidates
    BMesh elements once their BMesh wrapper is freed.

    per_shell: one group per shell, otherwise one group for the whole selection.
    whole_shells: use every loop of a shell that has any selected UV.
    """
    sync = is_sync(context)
    data = edit_meshes(context)
    groups = []
    if per_shell or whole_shells:
        for ob, bm, uv, faces in data:
            for isl in islands(faces, uv):
                loops = island_loops(isl)
                if not any(loop_selected(l, uv, sync) for l in loops):
                    continue
                if not whole_shells:
                    loops = [l for l in loops if loop_selected(l, uv, sync)]
                groups.append([(uv, loops)])
        if not per_shell and groups:
            groups = [[item for g in groups for item in g]]
    else:
        group = [(uv, selected_loops(faces, uv, sync)) for ob, bm, uv, faces in data]
        group = [(uv, loops) for uv, loops in group if loops]
        if group:
            groups.append(group)
    return groups, data


def group_bounds(group):
    lo = Vector((math.inf, math.inf))
    hi = Vector((-math.inf, -math.inf))
    for uv, loops in group:
        a, b = bounds(loops, uv)
        lo = Vector((min(lo.x, a.x), min(lo.y, a.y)))
        hi = Vector((max(hi.x, b.x), max(hi.y, b.y)))
    return lo, hi


def group_center(group):
    lo, hi = group_bounds(group)
    return (lo + hi) * 0.5


def pivot_point(context, group):
    mode = settings(context).pivot
    if mode == 'ORIGIN':
        return Vector((0.0, 0.0))
    if mode == 'CURSOR':
        space = context.space_data
        if space is not None and space.type == 'IMAGE_EDITOR':
            return Vector(space.cursor_location)
    return group_center(group)


def transform_selection(context, mat2):
    """Rotate / scale / flip the selection around the chosen pivot."""
    per_shell = settings(context).pivot == 'SHELL'
    groups, meshes = loop_groups(context, per_shell)
    for group in groups:
        pivot = pivot_point(context, group)
        for uv, loops in group:
            transform_loops(loops, uv, pivot, mat2)
    update_all(meshes)
    return bool(groups)


def selected_shells(context):
    """[(ob, uv, island)] for every shell that has selected UVs, and the edit meshes
    (keep them alive while using the islands)."""
    sync = is_sync(context)
    shells = []
    data = edit_meshes(context)
    for ob, bm, uv, faces in data:
        for isl in selected_islands(faces, uv, sync):
            shells.append((ob, uv, isl))
    return shells, data


# ---------------------------------------------------------------------------
# Writing selection
# ---------------------------------------------------------------------------

def _sticky_loops(l, uv, face_set):
    """Loops at the same vertex with the same UV (Blender's "shared location")."""
    co = l[uv].uv
    return [l2 for l2 in l.vert.link_loops if l2.face in face_set and same_uv(l2[uv].uv, co)]


def set_selection(context, bm, uv, faces, *, verts=(), edges=(), select_faces=(), state=True):
    """Select / deselect loop-verts, loop-edges and faces like a UV editor click would."""
    sync = is_sync(context)
    if sync:
        if getattr(bm, "uv_select_sync_valid", False):
            bm.uv_select_foreach_set(state, loop_verts=verts, loop_edges=edges, faces=select_faces)
            bm.uv_select_flush_mode()
            bm.uv_select_sync_to_mesh()
        else:
            for l in verts:
                l.vert.select_set(state)
            for l in edges:
                l.edge.select_set(state)
            for f in select_faces:
                f.select_set(state)
            bm.select_flush_mode()
        return

    face_set = set(faces)
    vert_loops = set()
    edge_loops = set()
    for f in select_faces:
        edge_loops.update(f.loops)
    for l in edges:
        edge_loops.add(l)
        l2 = l.link_loop_radial_next
        while l2 is not l:
            if l2.face in face_set and uv_edge_matches(l, l2, uv):
                edge_loops.add(l2)
            l2 = l2.link_loop_radial_next
    for l in edge_loops:
        vert_loops.add(l)
        vert_loops.add(l.link_loop_next)
    vert_loops.update(verts)
    for l in list(vert_loops):
        vert_loops.update(_sticky_loops(l, uv, face_set))

    new_api = hasattr(bmesh.types.BMLoop, "uv_select_vert_set")
    for l in vert_loops:
        if new_api:
            l.uv_select_vert_set(state)
        else:
            l[uv].select = state
    for l in edge_loops:
        if new_api:
            l.uv_select_edge_set(state)
        else:
            l[uv].select_edge = state
    if new_api:
        for f in select_faces:
            f.uv_select_set(state)
        # uv_select_flush_mode() follows bm.select_mode (the mesh mode), not the
        # UV mode, so flush with the UV mode or vertex mode would add every edge
        # between two selected UVs.
        uv_mode = context.scene.tool_settings.uv_select_mode
        mesh_mode = bm.select_mode
        bm.select_mode = {{'VERTEX': 'VERT'}.get(uv_mode, uv_mode)} - {'ISLAND'} or {'FACE'}
        bm.uv_select_flush_mode()
        bm.select_mode = mesh_mode


class UVOperator:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'


def rotation(angle):
    return Matrix.Rotation(angle, 2)
