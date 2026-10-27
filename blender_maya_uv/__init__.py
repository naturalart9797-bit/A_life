# Maya-style UV Toolkit for Blender's UV Editor.
#
# Sidebar (N) > "UV Toolkit" mirrors the sections of Maya's UV Toolkit:
# Pin / Select / Transform / Create / Cut and Sew / Unfold / Arrange and Layout /
# Texel Density. The UV Editor header gets Maya's Checker / Distortion / Shaded
# toggles, double-click selects a UV shell and Shift + double-click selects the
# shortest path.

bl_info = {
    "name": "Maya Style UV Toolkit",
    "author": "naturalart9797",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "UV Editor > Sidebar (N) > UV Toolkit, View3D > Sidebar (N) > Maya > UV",
    "description": "Maya-like UV Toolkit: unfold in place, cut and sew, align, stack, texel density, checker",
    "category": "UV",
}

import math

import bpy
import bmesh
from bpy.app.handlers import persistent
from mathutils import Matrix, Vector


EPS = 1e-6


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

class MayaUVSettings(bpy.types.PropertyGroup):
    move_step: bpy.props.FloatProperty(
        name="Step", description="Distance moved by the arrow buttons (0-1 UV space)",
        default=0.1, min=0.0, soft_max=1.0, precision=3)
    rotate_angle: bpy.props.FloatProperty(
        name="Angle", description="Angle used by the rotate buttons",
        default=math.radians(90.0), subtype='ANGLE')
    per_shell: bpy.props.BoolProperty(
        name="Per Shell", description="Rotate / flip each shell around its own center "
                                       "instead of the center of the whole selection",
        default=False)
    texture_size: bpy.props.IntProperty(
        name="Map Size", description="Texture resolution used for texel density",
        default=2048, min=1, soft_max=8192)
    texel_density: bpy.props.FloatProperty(
        name="Density", description="Texel density in pixels per Blender unit (meter)",
        default=1024.0, min=0.0, precision=2)
    unfold_method: bpy.props.EnumProperty(
        name="Method",
        items=[('ANGLE_BASED', "Angle Based", ""),
               ('CONFORMAL', "Conformal", ""),
               ('MINIMUM_STRETCH', "Minimum Stretch", "")],
        default='ANGLE_BASED')
    optimize_iterations: bpy.props.IntProperty(
        name="Iterations", default=100, min=1, soft_max=1000)
    layout_margin: bpy.props.FloatProperty(
        name="Shell Padding", default=0.005, min=0.0, max=1.0, precision=4)
    layout_rotate: bpy.props.BoolProperty(name="Rotate", default=True)
    checker_type: bpy.props.EnumProperty(
        name="Checker",
        items=[('UV_GRID', "Checker", "Black and white checker"),
               ('COLOR_GRID', "Color Grid", "Colored grid with labels")],
        default='UV_GRID',
        update=lambda self, context: _update_checker_image(self))
    checker_tiles: bpy.props.IntProperty(
        name="Resolution", description="Checker image resolution",
        default=1024, min=64, max=8192,
        update=lambda self, context: _update_checker_image(self))
    # Checker state (stored so toggling off restores the viewport after reloads).
    checker_on: bpy.props.BoolProperty(default=False)
    checker_prev_shading: bpy.props.StringProperty(default="")


def _settings(context):
    return context.scene.maya_uv


# ---------------------------------------------------------------------------
# UV helpers (work with both the 4.x and 5.x bmesh UV selection API)
# ---------------------------------------------------------------------------

def _sync(context):
    return context.scene.tool_settings.use_uv_select_sync


def _visible_faces(bm, sync):
    if sync:
        return [f for f in bm.faces if not f.hide]
    return [f for f in bm.faces if f.select and not f.hide]


def _loop_selected(loop, uv, sync):
    if sync:
        return loop.vert.select
    if hasattr(loop, "uv_select_vert"):  # Blender 5.0+
        return loop.uv_select_vert
    return loop[uv].select


def _edit_meshes(context):
    """[(object, bmesh, uv_layer, visible_faces)] for every mesh in edit mode."""
    sync = _sync(context)
    out = []
    objects = getattr(context, "objects_in_mode_unique_data", None) or (
        [context.edit_object] if context.edit_object else [])
    for ob in objects:
        if ob.type != 'MESH':
            continue
        bm = bmesh.from_edit_mesh(ob.data)
        uv = bm.loops.layers.uv.active
        if uv is None:
            continue
        out.append((ob, bm, uv, _visible_faces(bm, sync)))
    return out


def _update(ob):
    bmesh.update_edit_mesh(ob.data, loop_triangles=False, destructive=False)


def _selected_loops(faces, uv, sync):
    return [l for f in faces for l in f.loops if _loop_selected(l, uv, sync)]


def _islands(faces, uv):
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
            a1 = l[uv].uv
            b1 = l.link_loop_next[uv].uv
            l2 = l.link_loop_radial_next
            while l2 is not l:
                if l2.face in face_set:
                    if l2.vert is l.vert:
                        a2, b2 = l2[uv].uv, l2.link_loop_next[uv].uv
                    else:
                        a2, b2 = l2.link_loop_next[uv].uv, l2[uv].uv
                    if (a1 - a2).length_squared < EPS and (b1 - b2).length_squared < EPS:
                        ra, rb = find(f), find(l2.face)
                        if ra is not rb:
                            parent[ra] = rb
                l2 = l2.link_loop_radial_next

    groups = {}
    for f in faces:
        groups.setdefault(find(f), []).append(f)
    return list(groups.values())


def _selected_islands(faces, uv, sync):
    return [isl for isl in _islands(faces, uv)
            if any(_loop_selected(l, uv, sync) for f in isl for l in f.loops)]


def _bounds(loops, uv):
    us = [l[uv].uv.x for l in loops]
    vs = [l[uv].uv.y for l in loops]
    return Vector((min(us), min(vs))), Vector((max(us), max(vs)))


def _center(loops, uv):
    lo, hi = _bounds(loops, uv)
    return (lo + hi) * 0.5


def _uv_area(f, uv):
    pts = [l[uv].uv for l in f.loops]
    a = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        a += p.x * q.y - q.x * p.y
    return abs(a) * 0.5


def _world_area(f, mat):
    pts = [mat @ v.co for v in f.verts]
    a = Vector()
    for i in range(1, len(pts) - 1):
        a += (pts[i] - pts[0]).cross(pts[i + 1] - pts[0])
    return a.length * 0.5


def _island_loops(island):
    return [l for f in island for l in f.loops]


def _transform_loops(loops, uv, pivot, mat2):
    for l in loops:
        luv = l[uv]
        luv.uv = pivot + mat2 @ (luv.uv - pivot)


def _loop_groups(context, per_shell):
    """Yield (ob, uv, [loops]) groups to transform, plus the objects touched."""
    sync = _sync(context)
    data = _edit_meshes(context)
    groups = []
    if per_shell:
        for ob, bm, uv, faces in data:
            for isl in _islands(faces, uv):
                loops = [l for l in _island_loops(isl) if _loop_selected(l, uv, sync)]
                if loops:
                    groups.append([(uv, loops)])
    else:
        group = [(uv, _selected_loops(faces, uv, sync)) for ob, bm, uv, faces in data]
        group = [(uv, loops) for uv, loops in group if loops]
        if group:
            groups.append(group)
    return groups, [ob for ob, *_ in data]


def _group_center(group):
    lo = Vector((math.inf, math.inf))
    hi = Vector((-math.inf, -math.inf))
    for uv, loops in group:
        a, b = _bounds(loops, uv)
        lo = Vector((min(lo.x, a.x), min(lo.y, a.y)))
        hi = Vector((max(hi.x, b.x), max(hi.y, b.y)))
    return (lo + hi) * 0.5


def _op_available(idname):
    mod, name = idname.split(".")
    try:
        getattr(getattr(bpy.ops, mod), name).get_rna_type()
        return True
    except (AttributeError, KeyError):
        return False


class _UVOp:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'


# ---------------------------------------------------------------------------
# Transform
# ---------------------------------------------------------------------------

class UV_OT_maya_move(_UVOp, bpy.types.Operator):
    """Move the selected UVs by the step amount"""
    bl_idname = "uv.maya_move"
    bl_label = "Move UVs"

    direction: bpy.props.EnumProperty(
        items=[('UP', "Up", ""), ('DOWN', "Down", ""), ('LEFT', "Left", ""), ('RIGHT', "Right", "")])

    def execute(self, context):
        step = _settings(context).move_step
        offset = {'UP': (0, step), 'DOWN': (0, -step), 'LEFT': (-step, 0), 'RIGHT': (step, 0)}
        offset = Vector(offset[self.direction])
        groups, objects = _loop_groups(context, per_shell=False)
        for group in groups:
            for uv, loops in group:
                for l in loops:
                    l[uv].uv += offset
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


class UV_OT_maya_rotate(_UVOp, bpy.types.Operator):
    """Rotate the selected UVs around the selection (or shell) center"""
    bl_idname = "uv.maya_rotate"
    bl_label = "Rotate UVs"

    angle: bpy.props.FloatProperty(name="Angle", subtype='ANGLE', default=math.radians(90.0))

    def execute(self, context):
        mat2 = Matrix.Rotation(self.angle, 2)
        groups, objects = _loop_groups(context, _settings(context).per_shell)
        for group in groups:
            pivot = _group_center(group)
            for uv, loops in group:
                _transform_loops(loops, uv, pivot, mat2)
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


class UV_OT_maya_flip(_UVOp, bpy.types.Operator):
    """Flip the selected UVs in U or V"""
    bl_idname = "uv.maya_flip"
    bl_label = "Flip UVs"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])

    def execute(self, context):
        mat2 = Matrix(((-1, 0), (0, 1))) if self.axis == 'U' else Matrix(((1, 0), (0, -1)))
        groups, objects = _loop_groups(context, _settings(context).per_shell)
        for group in groups:
            pivot = _group_center(group)
            for uv, loops in group:
                _transform_loops(loops, uv, pivot, mat2)
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


class UV_OT_maya_align(_UVOp, bpy.types.Operator):
    """Align the selected UVs to the min / average / max U or V"""
    bl_idname = "uv.maya_align"
    bl_label = "Align UVs"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])
    mode: bpy.props.EnumProperty(items=[('MIN', "Min", ""), ('MEAN', "Average", ""), ('MAX', "Max", "")])

    def execute(self, context):
        i = 0 if self.axis == 'U' else 1
        groups, objects = _loop_groups(context, per_shell=False)
        for group in groups:
            values = [l[uv].uv[i] for uv, loops in group for l in loops]
            if self.mode == 'MIN':
                target = min(values)
            elif self.mode == 'MAX':
                target = max(values)
            else:
                target = sum(values) / len(values)
            for uv, loops in group:
                for l in loops:
                    co = l[uv].uv.copy()
                    co[i] = target
                    l[uv].uv = co
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


def _selected_shells(context):
    """[(ob, uv, island)] for every shell that has selected UVs."""
    sync = _sync(context)
    shells = []
    data = _edit_meshes(context)
    for ob, bm, uv, faces in data:
        for isl in _selected_islands(faces, uv, sync):
            shells.append((ob, uv, isl))
    return shells, [ob for ob, *_ in data]


def _move_shell(uv, island, offset):
    for l in _island_loops(island):
        l[uv].uv += offset


class UV_OT_maya_distribute(_UVOp, bpy.types.Operator):
    """Space the selected shells evenly in U or V (first and last stay in place)"""
    bl_idname = "uv.maya_distribute"
    bl_label = "Distribute Shells"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])

    def execute(self, context):
        i = 0 if self.axis == 'U' else 1
        shells, objects = _selected_shells(context)
        if len(shells) < 3:
            self.report({'WARNING'}, "Select at least 3 shells")
            return {'CANCELLED'}
        info = []
        for ob, uv, isl in shells:
            lo, hi = _bounds(_island_loops(isl), uv)
            info.append((lo[i], hi[i] - lo[i], uv, isl))
        info.sort(key=lambda t: t[0])
        start = info[0][0]
        end = info[-1][0] + info[-1][1]
        gap = (end - start - sum(t[1] for t in info)) / (len(info) - 1)
        pos = start
        for lo, size, uv, isl in info:
            offset = Vector((0.0, 0.0))
            offset[i] = pos - lo
            _move_shell(uv, isl, offset)
            pos += size + gap
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


class UV_OT_maya_stack(_UVOp, bpy.types.Operator):
    """Stack the selected shells on top of the largest one"""
    bl_idname = "uv.maya_stack"
    bl_label = "Stack Shells"

    def execute(self, context):
        shells, objects = _selected_shells(context)
        if len(shells) < 2:
            self.report({'WARNING'}, "Select at least 2 shells")
            return {'CANCELLED'}
        target = max(shells, key=lambda s: sum(_uv_area(f, s[1]) for f in s[2]))
        dst = _center(_island_loops(target[2]), target[1])
        for ob, uv, isl in shells:
            _move_shell(uv, isl, dst - _center(_island_loops(isl), uv))
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Create (projections)
# ---------------------------------------------------------------------------

def _fit_unit(points):
    """Scale/translate 2D points uniformly into the 0-1 square."""
    lo = Vector((min(p.x for p in points), min(p.y for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points)))
    size = max(hi.x - lo.x, hi.y - lo.y) or 1.0
    return [(p - lo) / size for p in points]


class UV_OT_maya_planar(_UVOp, bpy.types.Operator):
    """Planar projection of the selected faces (Maya: Create > Planar)"""
    bl_idname = "uv.maya_planar"
    bl_label = "Planar Projection"

    axis: bpy.props.EnumProperty(
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", ""),
               ('BEST', "Best Plane", "Project along the average normal of the faces")],
        default='BEST')

    def execute(self, context):
        any_faces = False
        for ob in getattr(context, "objects_in_mode_unique_data", [context.edit_object]):
            if ob.type != 'MESH':
                continue
            bm = bmesh.from_edit_mesh(ob.data)
            uv = bm.loops.layers.uv.verify()
            faces = [f for f in bm.faces if f.select and not f.hide]
            if not faces:
                continue
            any_faces = True
            if self.axis == 'BEST':
                normal = sum((f.normal * f.calc_area() for f in faces), Vector())
                if normal.length < EPS:
                    normal = Vector((0, 0, 1))
                rot = normal.normalized().to_track_quat('Z', 'Y').to_matrix().inverted()
                project = lambda co: (rot @ co).to_2d()
            else:
                keep = {'X': (1, 2), 'Y': (0, 2), 'Z': (0, 1)}[self.axis]
                project = lambda co: Vector((co[keep[0]], co[keep[1]]))
            loops = [l for f in faces for l in f.loops]
            for l, p in zip(loops, _fit_unit([project(l.vert.co) for l in loops])):
                l[uv].uv = p
            _update(ob)
        if not any_faces:
            self.report({'WARNING'}, "Select faces to project")
            return {'CANCELLED'}
        return {'FINISHED'}


def _view3d_override(context):
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            for region in area.regions:
                if region.type == 'WINDOW':
                    return {"area": area, "region": region}
    return None


class UV_OT_maya_view_project(_UVOp, bpy.types.Operator):
    """Projection that needs a 3D view (runs in the first 3D Viewport of this screen)"""
    bl_idname = "uv.maya_view_project"
    bl_label = "Projection"

    kind: bpy.props.EnumProperty(
        items=[('CAMERA', "Camera", "Project from the 3D view (Maya: Camera-based)"),
               ('CYLINDER', "Cylindrical", ""),
               ('SPHERE', "Spherical", "")])

    def execute(self, context):
        override = _view3d_override(context)
        if override is None:
            self.report({'WARNING'}, "Needs a 3D Viewport in this workspace")
            return {'CANCELLED'}
        with context.temp_override(**override):
            if self.kind == 'CAMERA':
                bpy.ops.uv.project_from_view(scale_to_bounds=True)
            elif self.kind == 'CYLINDER':
                bpy.ops.uv.cylinder_project(direction='ALIGN_TO_OBJECT', scale_to_bounds=True)
            else:
                bpy.ops.uv.sphere_project(direction='ALIGN_TO_OBJECT', scale_to_bounds=True)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Cut and Sew
# ---------------------------------------------------------------------------

class UV_OT_maya_sew(_UVOp, bpy.types.Operator):
    """Sew the selected UV edges together (Maya: Sew / Move and Sew)"""
    bl_idname = "uv.maya_sew"
    bl_label = "Sew"

    move: bpy.props.BoolProperty(
        name="Move", description="Move the smaller shell to the other one instead of "
                                 "meeting in the middle", default=False)

    def execute(self, context):
        # Stitch draws its preview in the region it runs in and crashes without
        # one, so run it in the UV editor's main region, not the sidebar.
        region = None
        if context.area is not None and context.area.type == 'IMAGE_EDITOR':
            region = next((r for r in context.area.regions if r.type == 'WINDOW'), None)
        if region is None:
            self.report({'WARNING'}, "Run this from the UV Editor")
            return {'CANCELLED'}
        mode = getattr(context.scene.tool_settings, "uv_select_mode", 'EDGE')
        with context.temp_override(region=region):
            bpy.ops.uv.stitch(
                'EXEC_DEFAULT',
                mode='EDGE' if mode == 'EDGE' else 'VERTEX',
                snap_islands=self.move, midpoint_snap=not self.move, clear_seams=True)
        return {'FINISHED'}


class UV_OT_maya_create_shell(_UVOp, bpy.types.Operator):
    """Cut the selected faces out into their own shell and mark the border as seams"""
    bl_idname = "uv.maya_create_shell"
    bl_label = "Create Shell"

    def execute(self, context):
        bpy.ops.uv.select_split()
        bpy.ops.uv.seams_from_islands(mark_seams=True)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Unfold
# ---------------------------------------------------------------------------

class UV_OT_maya_unfold(_UVOp, bpy.types.Operator):
    """Unfold the selected shells in place (keeps each shell's position and size, like Maya)"""
    bl_idname = "uv.maya_unfold"
    bl_label = "Unfold"

    def execute(self, context):
        s = _settings(context)
        before = {}
        for ob, bm, uv, faces in _edit_meshes(context):
            before[ob.name] = {f.index: [l[uv].uv.copy() for l in f.loops] for f in faces}

        # Blender unwraps per seam-bounded region; Maya unfolds per shell.
        # Mark the shell borders as seams so each shell unfolds on its own.
        bpy.ops.uv.seams_from_islands(mark_seams=True)
        bpy.ops.uv.unwrap(method=s.unfold_method, margin=0.0)

        for ob, bm, uv, faces in _edit_meshes(context):
            old = before.get(ob.name)
            if old is None:
                continue
            bm.faces.ensure_lookup_table()
            changed = {f for f in faces
                       if f.index in old and any((l[uv].uv - o).length_squared > EPS
                                                 for l, o in zip(f.loops, old[f.index]))}
            for isl in _islands(faces, uv):
                if not changed.issuperset(isl):
                    continue  # pinned / untouched parts keep where unwrap put them
                loops = _island_loops(isl)
                if any(l[uv].pin_uv for l in loops):
                    continue
                old_pts = [p for f in isl for p in old[f.index]]
                old_lo = Vector((min(p.x for p in old_pts), min(p.y for p in old_pts)))
                old_hi = Vector((max(p.x for p in old_pts), max(p.y for p in old_pts)))
                old_area = sum(_poly_area(old[f.index]) for f in isl)
                new_area = sum(_uv_area(f, uv) for f in isl)
                if new_area < EPS * EPS or old_area < EPS * EPS:
                    continue
                scale = math.sqrt(old_area / new_area)
                pivot = _center(loops, uv)
                dst = (old_lo + old_hi) * 0.5
                for l in loops:
                    l[uv].uv = dst + (l[uv].uv - pivot) * scale
            _update(ob)
        return {'FINISHED'}


def _poly_area(pts):
    a = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        a += p.x * q.y - q.x * p.y
    return abs(a) * 0.5


class UV_OT_maya_optimize(_UVOp, bpy.types.Operator):
    """Relax the selected UVs to reduce distortion (Maya: Optimize)"""
    bl_idname = "uv.maya_optimize"
    bl_label = "Optimize"

    def execute(self, context):
        bpy.ops.uv.minimize_stretch(iterations=_settings(context).optimize_iterations)
        return {'FINISHED'}


class UV_OT_maya_layout(_UVOp, bpy.types.Operator):
    """Pack the selected shells into 0-1 space (Maya: Layout)"""
    bl_idname = "uv.maya_layout"
    bl_label = "Layout"

    def execute(self, context):
        s = _settings(context)
        bpy.ops.uv.pack_islands(margin=s.layout_margin, rotate=s.layout_rotate)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Texel density
# ---------------------------------------------------------------------------

def _density(shells, size):
    uv_area = sum(_uv_area(f, uv) for ob, uv, isl in shells for f in isl)
    world_area = sum(_world_area(f, ob.matrix_world) for ob, uv, isl in shells for f in isl)
    if world_area < EPS * EPS:
        return 0.0
    return math.sqrt(uv_area / world_area) * size


class UV_OT_maya_texel_get(_UVOp, bpy.types.Operator):
    """Read the texel density of the selected shells"""
    bl_idname = "uv.maya_texel_get"
    bl_label = "Get"

    def execute(self, context):
        s = _settings(context)
        shells, _objects = _selected_shells(context)
        if not shells:
            self.report({'WARNING'}, "Select UVs first")
            return {'CANCELLED'}
        s.texel_density = _density(shells, s.texture_size)
        return {'FINISHED'}


class UV_OT_maya_texel_set(_UVOp, bpy.types.Operator):
    """Scale each selected shell to the texel density (around its own center)"""
    bl_idname = "uv.maya_texel_set"
    bl_label = "Set"

    def execute(self, context):
        s = _settings(context)
        shells, objects = _selected_shells(context)
        if not shells:
            self.report({'WARNING'}, "Select UVs first")
            return {'CANCELLED'}
        for shell in shells:
            current = _density([shell], s.texture_size)
            if current <= 0.0:
                continue
            ob, uv, isl = shell
            loops = _island_loops(isl)
            pivot = _center(loops, uv)
            _transform_loops(loops, uv, pivot, Matrix.Diagonal((1, 1)) * (s.texel_density / current))
        for ob in objects:
            _update(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Display: checker
# ---------------------------------------------------------------------------

CHECKER_NAME = "MayaUV_Checker"


def _checker_image(settings):
    img = bpy.data.images.get(CHECKER_NAME)
    if img is None:
        img = bpy.data.images.new(CHECKER_NAME, settings.checker_tiles, settings.checker_tiles)
    if img.generated_type != settings.checker_type:
        img.generated_type = settings.checker_type
    if img.generated_width != settings.checker_tiles:
        img.generated_width = img.generated_height = settings.checker_tiles
    return img


def _update_checker_image(settings):
    if bpy.data.images.get(CHECKER_NAME) is not None:
        _checker_image(settings)


def _checker_material(settings):
    mat = bpy.data.materials.get(CHECKER_NAME)
    if mat is None:
        mat = bpy.data.materials.new(CHECKER_NAME)
        if hasattr(mat, "use_nodes") and not mat.use_nodes:
            mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is None:
            bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
            out = next((n for n in nt.nodes if n.type == 'OUTPUT_MATERIAL'), None) or \
                nt.nodes.new("ShaderNodeOutputMaterial")
            nt.links.new(bsdf.outputs[0], out.inputs[0])
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.location = (bsdf.location.x - 300, bsdf.location.y)
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        nt.nodes.active = tex
    tex = next(n for n in mat.node_tree.nodes if n.type == 'TEX_IMAGE')
    tex.image = _checker_image(settings)
    return mat


def _view3d_spaces(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield area.spaces.active


class MAYA_UV_OT_checker(bpy.types.Operator):
    """Show a checker texture on all objects (Maya: Checkered tiles)"""
    bl_idname = "maya_uv.checker"
    bl_label = "Checker"
    bl_options = {'REGISTER'}

    def execute(self, context):
        s = _settings(context)
        view_layer = context.view_layer
        if not s.checker_on:
            view_layer.material_override = _checker_material(s)
            spaces = list(_view3d_spaces(context))
            s.checker_prev_shading = ",".join(sp.shading.type for sp in spaces)
            for sp in spaces:
                if sp.shading.type in {'SOLID', 'WIREFRAME'}:
                    sp.shading.type = 'MATERIAL'
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR' and area.spaces.active.image is None:
                    area.spaces.active.image = _checker_image(s)
            s.checker_on = True
        else:
            if view_layer.material_override and view_layer.material_override.name == CHECKER_NAME:
                view_layer.material_override = None
            prev = s.checker_prev_shading.split(",") if s.checker_prev_shading else []
            for sp, shading in zip(_view3d_spaces(context), prev):
                if shading:
                    sp.shading.type = shading
            for area in context.screen.areas:
                if area.type == 'IMAGE_EDITOR':
                    img = area.spaces.active.image
                    if img is not None and img.name == CHECKER_NAME:
                        area.spaces.active.image = None
            s.checker_on = False
        return {'FINISHED'}


class MAYA_UV_OT_open_editor(bpy.types.Operator):
    """Switch to the UV Editing workspace"""
    bl_idname = "maya_uv.open_editor"
    bl_label = "UV Editor"

    def execute(self, context):
        ws = bpy.data.workspaces.get("UV Editing")
        if ws is None:
            self.report({'WARNING'}, "No 'UV Editing' workspace in this file")
            return {'CANCELLED'}
        context.window.workspace = ws
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Shift + double-click shortest path in the UV editor
# ---------------------------------------------------------------------------

# The UV active element is replaced by every click (its history is cleared),
# so remember the previous one: mesh pointer -> [previous, current] snapshots.
_uv_active = {}


def _history_snapshot(bm):
    return tuple((type(e).__name__, e.index) for e in bm.select_history)


@persistent
def _track_uv_active(scene, depsgraph=None):
    ob = bpy.context.view_layer.objects.active if bpy.context.view_layer else None
    if ob is None or ob.type != 'MESH' or ob.mode != 'EDIT':
        return
    bm = bmesh.from_edit_mesh(ob.data)
    snap = _history_snapshot(bm)
    hist = _uv_active.setdefault(ob.data.as_pointer(), [(), ()])
    if snap and snap != hist[1]:
        hist[0], hist[1] = hist[1], snap


def _restore_snapshot(bm, snap):
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
            if _restore_snapshot(bm, prev):
                _update(ob)
        result = bpy.ops.uv.shortest_path_pick('INVOKE_DEFAULT')
        return {'FINISHED'} if 'FINISHED' in result else {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

class _UVPanel:
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "UV Toolkit"

    @classmethod
    def poll(cls, context):
        sp = context.space_data
        return context.mode == 'EDIT_MESH' and getattr(sp, "mode", 'UV') == 'UV'


class IMAGE_PT_maya_uv_toolkit(_UVPanel, bpy.types.Panel):
    bl_label = "UV Toolkit"

    def draw(self, context):
        s = _settings(context)
        row = self.layout.row(align=True)
        row.operator(MAYA_UV_OT_checker.bl_idname, text="Checker", icon='TEXTURE', depress=s.checker_on)
        uv_editor = context.space_data.uv_editor
        row.prop(uv_editor, "show_stretch", text="Distortion", toggle=True, icon='MOD_UVPROJECT')


class IMAGE_PT_maya_uv_pin(_UVPanel, bpy.types.Panel):
    bl_label = "Pin"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        row = self.layout.row(align=True)
        row.operator("uv.pin", text="Pin", icon='PINNED').clear = False
        row.operator("uv.pin", text="Unpin", icon='UNPINNED').clear = True
        row = self.layout.row(align=True)
        op = row.operator("uv.pin", text="Invert")
        op.invert = True
        row.operator("uv.select_pinned", text="Select Pinned")


class IMAGE_PT_maya_uv_select(_UVPanel, bpy.types.Panel):
    bl_label = "Select"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Double-click: Shell   Shift+Double-click: Path")
        row = layout.row(align=True)
        row.operator("uv.select_linked", text="Shell")
        row.operator("uv.select_overlap", text="Overlapping")
        row = layout.row(align=True)
        row.operator("uv.select_more", text="Grow")
        row.operator("uv.select_less", text="Shrink")


class IMAGE_PT_maya_uv_transform(_UVPanel, bpy.types.Panel):
    bl_label = "Transform"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout

        # Move arrows
        grid = layout.grid_flow(columns=3, align=True)
        grid.label(text="")
        grid.operator(UV_OT_maya_move.bl_idname, text="", icon='TRIA_UP').direction = 'UP'
        grid.label(text="")
        grid.operator(UV_OT_maya_move.bl_idname, text="", icon='TRIA_LEFT').direction = 'LEFT'
        grid.prop(s, "move_step", text="")
        grid.operator(UV_OT_maya_move.bl_idname, text="", icon='TRIA_RIGHT').direction = 'RIGHT'
        grid.label(text="")
        grid.operator(UV_OT_maya_move.bl_idname, text="", icon='TRIA_DOWN').direction = 'DOWN'
        grid.label(text="")

        # Rotate / flip
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator(UV_OT_maya_rotate.bl_idname, text="", icon='LOOP_FORWARDS').angle = -s.rotate_angle
        row.prop(s, "rotate_angle", text="")
        row.operator(UV_OT_maya_rotate.bl_idname, text="", icon='LOOP_BACK').angle = s.rotate_angle
        row = col.row(align=True)
        row.operator(UV_OT_maya_flip.bl_idname, text="Flip U").axis = 'U'
        row.operator(UV_OT_maya_flip.bl_idname, text="Flip V").axis = 'V'
        col.prop(s, "per_shell")

        # Align
        layout.label(text="Align")
        for axis in ('U', 'V'):
            row = layout.row(align=True)
            for mode, label in (('MIN', "Min"), ('MEAN', "Avg"), ('MAX', "Max")):
                op = row.operator(UV_OT_maya_align.bl_idname, text=f"{label} {axis}")
                op.axis, op.mode = axis, mode

        # Straighten / distribute / orient / stack
        layout.label(text="Straighten")
        row = layout.row(align=True)
        row.operator("uv.align", text="UV").axis = 'ALIGN_S'
        row.operator("uv.align", text="U").axis = 'ALIGN_T'
        row.operator("uv.align", text="V").axis = 'ALIGN_U'

        layout.label(text="Distribute")
        row = layout.row(align=True)
        row.operator(UV_OT_maya_distribute.bl_idname, text="U").axis = 'U'
        row.operator(UV_OT_maya_distribute.bl_idname, text="V").axis = 'V'

        layout.label(text="Orient / Stack")
        row = layout.row(align=True)
        if _op_available("uv.align_rotation"):
            row.operator("uv.align_rotation", text="Orient Shells").method = 'AUTO'
            row.operator("uv.align_rotation", text="Orient to Edge").method = 'EDGE'
        row = layout.row(align=True)
        row.operator(UV_OT_maya_stack.bl_idname, text="Stack")
        row.operator(UV_OT_maya_layout.bl_idname, text="Unstack")


class IMAGE_PT_maya_uv_create(_UVPanel, bpy.types.Panel):
    bl_label = "Create"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        _draw_create(self.layout)


def _draw_create(layout):
    layout.label(text="Planar")
    row = layout.row(align=True)
    for axis in ('X', 'Y', 'Z'):
        row.operator(UV_OT_maya_planar.bl_idname, text=axis).axis = axis
    row.operator(UV_OT_maya_planar.bl_idname, text="Best").axis = 'BEST'
    col = layout.column(align=True)
    col.operator(UV_OT_maya_view_project.bl_idname, text="Camera-based").kind = 'CAMERA'
    row = col.row(align=True)
    row.operator(UV_OT_maya_view_project.bl_idname, text="Cylindrical").kind = 'CYLINDER'
    row.operator(UV_OT_maya_view_project.bl_idname, text="Spherical").kind = 'SPHERE'
    row = col.row(align=True)
    row.operator("uv.cube_project", text="Cube")
    row.operator("uv.smart_project", text="Automatic")


class IMAGE_PT_maya_uv_cut_sew(_UVPanel, bpy.types.Panel):
    bl_label = "Cut and Sew"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("uv.mark_seam", text="Cut", icon='SCULPTMODE_HLT').clear = False
        row.operator(UV_OT_maya_sew.bl_idname, text="Sew").move = False
        row.operator(UV_OT_maya_sew.bl_idname, text="Move and Sew").move = True
        row = layout.row(align=True)
        row.operator("uv.select_split", text="Split")
        row.operator(UV_OT_maya_create_shell.bl_idname, text="Create Shell")
        layout.operator("uv.seams_from_islands", text="Seams from Shells")


class IMAGE_PT_maya_uv_unfold(_UVPanel, bpy.types.Panel):
    bl_label = "Unfold"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator(UV_OT_maya_unfold.bl_idname, text="Unfold")
        row.prop(s, "unfold_method", text="")
        row = layout.row(align=True)
        row.operator(UV_OT_maya_optimize.bl_idname, text="Optimize")
        row.prop(s, "optimize_iterations", text="")


class IMAGE_PT_maya_uv_layout(_UVPanel, bpy.types.Panel):
    bl_label = "Arrange and Layout"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator(UV_OT_maya_layout.bl_idname, text="Layout")
        row.prop(s, "layout_margin", text="Padding")
        layout.prop(s, "layout_rotate")
        layout.operator("uv.average_islands_scale", text="Normalize Scale")


class IMAGE_PT_maya_uv_texel(_UVPanel, bpy.types.Panel):
    bl_label = "Texel Density"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.prop(s, "texture_size")
        row = layout.row(align=True)
        row.prop(s, "texel_density", text="px/m")
        row.operator(UV_OT_maya_texel_get.bl_idname, text="Get")
        row.operator(UV_OT_maya_texel_set.bl_idname, text="Set")


class IMAGE_PT_maya_uv_display(_UVPanel, bpy.types.Panel):
    bl_label = "Display"
    bl_parent_id = "IMAGE_PT_maya_uv_toolkit"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        layout.prop(s, "checker_type", text="")
        layout.prop(s, "checker_tiles")
        uv_editor = context.space_data.uv_editor
        layout.prop(uv_editor, "display_stretch_type", text="Distortion")
        layout.prop(uv_editor, "show_faces", text="Shaded")


def _draw_header(self, context):
    sp = context.space_data
    if context.mode != 'EDIT_MESH' or getattr(sp, "mode", 'UV') != 'UV':
        return
    s = _settings(context)
    row = self.layout.row(align=True)
    row.operator(MAYA_UV_OT_checker.bl_idname, text="", icon='TEXTURE', depress=s.checker_on)
    row.prop(sp.uv_editor, "show_stretch", text="", icon='MOD_UVPROJECT')


class VIEW3D_PT_maya_uv(bpy.types.Panel):
    bl_label = "UV"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Maya"

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_MESH'

    def draw(self, context):
        s = _settings(context)
        layout = self.layout
        row = layout.row(align=True)
        row.operator(MAYA_UV_OT_open_editor.bl_idname, icon='UV')
        row.operator(MAYA_UV_OT_checker.bl_idname, text="Checker", icon='TEXTURE', depress=s.checker_on)
        row = layout.row(align=True)
        row.operator("mesh.mark_seam", text="Cut").clear = False
        row.operator("mesh.mark_seam", text="Sew").clear = True
        _draw_create(layout)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

classes = (
    MayaUVSettings,
    UV_OT_maya_move,
    UV_OT_maya_rotate,
    UV_OT_maya_flip,
    UV_OT_maya_align,
    UV_OT_maya_distribute,
    UV_OT_maya_stack,
    UV_OT_maya_planar,
    UV_OT_maya_view_project,
    UV_OT_maya_sew,
    UV_OT_maya_create_shell,
    UV_OT_maya_unfold,
    UV_OT_maya_optimize,
    UV_OT_maya_layout,
    UV_OT_maya_texel_get,
    UV_OT_maya_texel_set,
    MAYA_UV_OT_checker,
    MAYA_UV_OT_open_editor,
    UV_OT_maya_path_select,
    IMAGE_PT_maya_uv_toolkit,
    IMAGE_PT_maya_uv_pin,
    IMAGE_PT_maya_uv_select,
    IMAGE_PT_maya_uv_transform,
    IMAGE_PT_maya_uv_create,
    IMAGE_PT_maya_uv_cut_sew,
    IMAGE_PT_maya_uv_unfold,
    IMAGE_PT_maya_uv_layout,
    IMAGE_PT_maya_uv_texel,
    IMAGE_PT_maya_uv_display,
    VIEW3D_PT_maya_uv,
)

_keymaps = []


def _register_keymaps(select_mouse):
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    # DOUBLE_CLICK is matched before being retried as PRESS, so these win over
    # the active tool's click-select.
    km = kc.keymaps.new(name="UV Editor", space_type='EMPTY')
    kmi = km.keymap_items.new("uv.select_linked_pick", select_mouse, 'DOUBLE_CLICK')
    kmi.properties.extend = False
    kmi.properties.deselect = False
    _keymaps.append((km, kmi))
    kmi = km.keymap_items.new(UV_OT_maya_path_select.bl_idname, select_mouse, 'DOUBLE_CLICK', shift=True)
    _keymaps.append((km, kmi))


def _select_mouse():
    prefs = getattr(bpy.context.window_manager.keyconfigs.active, "preferences", None)
    return 'RIGHTMOUSE' if getattr(prefs, "select_mouse", 'LEFT') == 'RIGHT' else 'LEFTMOUSE'


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.maya_uv = bpy.props.PointerProperty(type=MayaUVSettings)
    bpy.types.IMAGE_HT_header.append(_draw_header)
    bpy.app.handlers.depsgraph_update_post.append(_track_uv_active)
    _register_keymaps(_select_mouse())


def unregister():
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    if _track_uv_active in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_track_uv_active)
    bpy.types.IMAGE_HT_header.remove(_draw_header)
    del bpy.types.Scene.maya_uv
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    _uv_active.clear()


if __name__ == "__main__":
    register()
