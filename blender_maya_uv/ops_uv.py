# Create / Cut and Sew / Unfold / Arrange and Layout / Measure / UV Sets / Tools
# sections of the UV Toolkit.

import math

import bpy
import bmesh
from mathutils import Matrix, Vector

from .common import (
    UVOperator, EPS, settings, update_all, is_sync, edit_meshes, edit_objects, update, islands,
    island_loops, selected_shells, uv_area, world_area, poly_area, transform_loops, center,
    loop_selected, loop_edge_selected, face_selected, call_op, rotation, uv_edge_matches,
)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _fit_unit(points):
    """Scale/translate 2D points uniformly into the 0-1 square."""
    lo = Vector((min(p.x for p in points), min(p.y for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points)))
    size = max(hi.x - lo.x, hi.y - lo.y) or 1.0
    return [(p - lo) / size for p in points]


class UV_OT_maya_planar(UVOperator, bpy.types.Operator):
    """Planar projection of the selected faces (Maya: Create > Planar)"""
    bl_idname = "uv.maya_planar"
    bl_label = "Planar Projection"

    axis: bpy.props.EnumProperty(
        items=[('X', "X", ""), ('Y', "Y", ""), ('Z', "Z", ""),
               ('BEST', "Best Plane", "Project along the average normal of the faces")],
        default='BEST')

    def execute(self, context):
        any_faces = False
        for ob in edit_objects(context):
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
            update(ob)
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


class UV_OT_maya_view_project(UVOperator, bpy.types.Operator):
    """Projection that needs a 3D view (runs in the first 3D Viewport of this screen)"""
    bl_idname = "uv.maya_view_project"
    bl_label = "Projection"

    kind: bpy.props.EnumProperty(
        items=[('CAMERA', "Camera", "Project from the 3D view (Maya: Camera-based)"),
               ('CYLINDER', "Cylindrical", ""),
               ('SPHERE', "Spherical", "")])

    def execute(self, context):
        if context.area is not None and context.area.type == 'VIEW_3D':
            override = {}
        else:
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


class UV_OT_maya_auto_seams(UVOperator, bpy.types.Operator):
    """Cut the selected faces along sharp edges and the selection border, then unfold
    and lay them out (Maya: Auto Seams)"""
    bl_idname = "uv.maya_auto_seams"
    bl_label = "Auto Seams"

    def execute(self, context):
        s = settings(context)
        found = False
        for ob in edit_objects(context):
            bm = bmesh.from_edit_mesh(ob.data)
            faces = {f for f in bm.faces if f.select and not f.hide}
            if not faces:
                continue
            found = True
            for e in {e for f in faces for e in f.edges}:
                linked = [f for f in e.link_faces if f in faces]
                if len(linked) != 2 or len(e.link_faces) != 2:
                    e.seam = True
                elif e.calc_face_angle(0.0) > s.auto_seam_angle:
                    e.seam = True
            update(ob)
        if not found:
            self.report({'WARNING'}, "Select faces first")
            return {'CANCELLED'}
        bpy.ops.uv.unwrap(method=s.unfold_method, margin=s.layout_margin)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Cut and Sew
# ---------------------------------------------------------------------------

def _sew_pairs(faces, uv, sync):
    """(selected loop, partner loop) for every selected UV edge that is split in UV space:
    the same mesh edge whose other face has its UVs somewhere else."""
    face_set = set(faces)
    pairs = []
    seen = set()
    for f in faces:
        for l in f.loops:
            if l.edge.index in seen:
                continue
            if not (loop_edge_selected(l, uv, sync) or (
                    loop_selected(l, uv, sync) and loop_selected(l.link_loop_next, uv, sync))):
                continue
            for l2 in l.edge.link_loops:
                if l2 is not l and l2.face in face_set and not uv_edge_matches(l, l2, uv):
                    pairs.append((l, l2))
                    seen.add(l.edge.index)
                    break
    return pairs


def _corners(la, lb):
    """Pairs of loops at the same mesh vertex on the two sides of an edge."""
    if lb.vert is la.vert:
        return ((la, lb), (la.link_loop_next, lb.link_loop_next))
    return ((la, lb.link_loop_next), (la.link_loop_next, lb))


def _similarity(src, dst):
    """Rotation + uniform scale + translation that best maps src points onto dst."""
    cs = sum(src, Vector((0.0, 0.0))) / len(src)
    cd = sum(dst, Vector((0.0, 0.0))) / len(dst)
    num = den = ss = 0.0
    for p, q in zip(src, dst):
        p, q = p - cs, q - cd
        num += p.x * q.y - p.y * q.x
        den += p.x * q.x + p.y * q.y
        ss += p.length_squared
    if ss < EPS * EPS:
        return Matrix.Identity(2), cs, cd  # a single point: just move
    angle = math.atan2(num, den)
    scale = math.hypot(num, den) / ss
    return rotation(angle) * scale, cs, cd


def sew(context, move):
    """Sew the selected UV edges to their partners. Returns the number of edges sewn."""
    sync = is_sync(context)
    sewn = 0
    for ob, bm, uv, faces in edit_meshes(context):
        pairs = _sew_pairs(faces, uv, sync)
        if not pairs:
            continue
        shells = islands(faces, uv)
        shell_of = {f: i for i, isl in enumerate(shells) for f in isl}

        moved = set()
        if move:
            # Move each partner shell onto its selected side (the smaller shell moves).
            groups = {}
            for la, lb in pairs:
                a, b = shell_of[la.face], shell_of[lb.face]
                if a != b:
                    groups.setdefault((a, b), []).append((la, lb))
            for (a, b), group in groups.items():
                area = lambda i: sum(uv_area(f, uv) for f in shells[i])
                # The smaller shell moves; on a tie, the partner of the selected edge.
                mover = a if area(a) < area(b) * (1.0 - 1e-6) else b
                if mover in moved:
                    continue
                src, dst = [], []
                for la, lb in group:
                    for ca, cb in _corners(la, lb):
                        fixed, moving = (ca, cb) if mover == b else (cb, ca)
                        src.append(moving[uv].uv.copy())
                        dst.append(fixed[uv].uv.copy())
                mat2, cs, cd = _similarity(src, dst)
                for l in island_loops(shells[mover]):
                    l[uv].uv = cd + mat2 @ (l[uv].uv - cs)
                moved.add(mover)

        # Merge the UVs on both sides of every edge: a UV vertex is a mesh vertex
        # at one UV position, shared by all its loops.
        def node(l):
            co = l[uv].uv
            return (l.vert.index, round(co.x, 6), round(co.y, 6))

        loops_of = {}
        for f in faces:
            for l in f.loops:
                loops_of.setdefault(node(l), []).append(l)
        parent = {}

        def find(n):
            parent.setdefault(n, n)
            while parent[n] != n:
                parent[n] = parent[parent[n]]
                n = parent[n]
            return n

        fixed_nodes = set()
        for la, lb in pairs:
            for ca, cb in _corners(la, lb):
                na, nb = node(ca), node(cb)
                parent[find(na)] = find(nb)
                if move:
                    for l, n in ((ca, na), (cb, nb)):
                        if shell_of[l.face] not in moved:
                            fixed_nodes.add(n)
        members = {}
        for n in list(parent):
            members.setdefault(find(n), []).append(n)
        for group in members.values():
            anchor = [n for n in group if n in fixed_nodes] or group
            target = Vector((sum(n[1] for n in anchor) / len(anchor),
                             sum(n[2] for n in anchor) / len(anchor)))
            # Use the exact UV of an anchor loop, not the rounded key.
            exact = [loops_of[n][0][uv].uv for n in anchor if loops_of.get(n)]
            if exact:
                target = sum(exact, Vector((0.0, 0.0))) / len(exact)
            for n in group:
                for l in loops_of.get(n, ()):
                    l[uv].uv = target
        for la, lb in pairs:
            la.edge.seam = False
        sewn += len(pairs)
        update(ob)
    return sewn


class UV_OT_maya_sew(UVOperator, bpy.types.Operator):
    """Sew the selected UV edges to the matching edges of the other shell
    (Maya: Sew / Move and Sew). Select the edges on one side only"""
    bl_idname = "uv.maya_sew"
    bl_label = "Sew"

    move: bpy.props.BoolProperty(
        name="Move", description="Move the smaller shell onto the other one (keeping its "
                                 "shape) instead of meeting in the middle", default=False)

    def execute(self, context):
        if not sew(context, self.move):
            self.report({'WARNING'}, "Select UV edges on a shell border (one side is enough)")
            return {'CANCELLED'}
        return {'FINISHED'}


def _ensure_active_face(context):
    """follow_active_quads needs an active, selected quad."""
    sync = is_sync(context)
    for ob, bm, uv, faces in edit_meshes(context):
        active = bm.faces.active
        if active is not None and active in faces and face_selected(active, uv, sync):
            return True
        for f in faces:
            if len(f.verts) == 4 and face_selected(f, uv, sync):
                bm.faces.active = f
                update(ob)
                return True
    return False


class UV_OT_maya_create_shell(UVOperator, bpy.types.Operator):
    """Cut the selected faces out into their own shell and mark its border as seams
    (Maya: Create Shell / Create Shell (Grid))"""
    bl_idname = "uv.maya_create_shell"
    bl_label = "Create Shell"

    grid: bpy.props.BoolProperty(name="Grid", description="Straighten the shell into a grid",
                                 default=False)

    def execute(self, context):
        bpy.ops.uv.select_split()
        bpy.ops.uv.seams_from_islands(mark_seams=True)
        if self.grid:
            if not _ensure_active_face(context):
                self.report({'WARNING'}, "Grid needs a selected quad")
                return {'FINISHED'}
            bpy.ops.uv.follow_active_quads(mode='LENGTH_AVERAGE')
        return {'FINISHED'}


class UV_OT_maya_straighten_shell(UVOperator, bpy.types.Operator):
    """Straighten the selected quad shells into a grid (Maya: Straighten Shell)"""
    bl_idname = "uv.maya_straighten_shell"
    bl_label = "Straighten Shell"

    def execute(self, context):
        if not _ensure_active_face(context):
            self.report({'WARNING'}, "Select quads first")
            return {'CANCELLED'}
        bpy.ops.uv.follow_active_quads(mode='LENGTH_AVERAGE')
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Unfold
# ---------------------------------------------------------------------------

def _best_rotation(old_pts, new_pts):
    """Angle that best rotates the centered new points onto the centered old points."""
    co = sum(old_pts, Vector((0.0, 0.0))) / len(old_pts)
    cn = sum(new_pts, Vector((0.0, 0.0))) / len(new_pts)
    num = den = 0.0
    for p, q in zip(old_pts, new_pts):
        p = p - co
        q = q - cn
        num += q.x * p.y - q.y * p.x
        den += q.x * p.x + q.y * p.y
    return math.atan2(num, den), co, cn


class UV_OT_maya_unfold(UVOperator, bpy.types.Operator):
    """Unfold the selected shells in place, keeping each shell's position, size and
    orientation like Maya. Pinned UVs stay where they are"""
    bl_idname = "uv.maya_unfold"
    bl_label = "Unfold"

    def execute(self, context):
        s = settings(context)
        before = {}
        for ob, bm, uv, faces in edit_meshes(context):
            before[ob.name] = {f.index: [l[uv].uv.copy() for l in f.loops] for f in faces}

        # Blender unwraps per seam-bounded region; Maya unfolds per shell.
        # Mark the shell borders as seams so each shell unfolds on its own.
        bpy.ops.uv.seams_from_islands(mark_seams=True)
        bpy.ops.uv.unwrap(method=s.unfold_method, margin=0.0)

        for ob, bm, uv, faces in edit_meshes(context):
            old = before.get(ob.name)
            if old is None:
                continue
            changed = {f for f in faces
                       if f.index in old and any((l[uv].uv - o).length_squared > EPS
                                                 for l, o in zip(f.loops, old[f.index]))}
            for isl in islands(faces, uv):
                if not changed.issuperset(isl):
                    continue
                loops = island_loops(isl)
                if any(l[uv].pin_uv for l in loops):
                    continue  # pinned shells are already placed by their pins
                old_pts = [p for f in isl for p in old[f.index]]
                new_pts = [l[uv].uv.copy() for l in loops]
                old_area = sum(poly_area(old[f.index]) for f in isl)
                new_area = sum(uv_area(f, uv) for f in isl)
                if new_area < EPS * EPS or old_area < EPS * EPS:
                    continue
                angle, co, cn = _best_rotation(old_pts, new_pts)
                mat2 = rotation(angle) * math.sqrt(old_area / new_area)
                for l, q, p in zip(loops, new_pts, old_pts):
                    r = co + mat2 @ (q - cn)
                    if s.unfold_constraint == 'U':
                        r.y = p.y
                    elif s.unfold_constraint == 'V':
                        r.x = p.x
                    l[uv].uv = r
            update(ob)
        return {'FINISHED'}


class UV_OT_maya_optimize(UVOperator, bpy.types.Operator):
    """Relax the selected UVs to reduce distortion (Maya: Optimize)"""
    bl_idname = "uv.maya_optimize"
    bl_label = "Optimize"

    def execute(self, context):
        bpy.ops.uv.minimize_stretch(iterations=settings(context).optimize_iterations)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Arrange and Layout
# ---------------------------------------------------------------------------

class UV_OT_maya_layout(UVOperator, bpy.types.Operator):
    """Pack the selected shells into the 0-1 tile (Maya: Layout)"""
    bl_idname = "uv.maya_layout"
    bl_label = "Layout"

    def execute(self, context):
        s = settings(context)
        call_op("uv.pack_islands", margin=s.layout_margin, rotate=s.layout_rotate,
                scale=s.layout_scale, shape_method=s.layout_shape)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Measure / texel density
# ---------------------------------------------------------------------------

def _density(shells, size):
    area_uv = sum(uv_area(f, uv) for ob, uv, isl in shells for f in isl)
    area_3d = sum(world_area(f, ob.matrix_world) for ob, uv, isl in shells for f in isl)
    if area_3d < EPS * EPS:
        return 0.0
    return math.sqrt(area_uv / area_3d) * size


class UV_OT_maya_texel_get(UVOperator, bpy.types.Operator):
    """Read the texel density of the selected shells"""
    bl_idname = "uv.maya_texel_get"
    bl_label = "Get"

    def execute(self, context):
        s = settings(context)
        shells, _objects = selected_shells(context)
        if not shells:
            self.report({'WARNING'}, "Select UVs first")
            return {'CANCELLED'}
        s.texel_density = _density(shells, s.texture_size)
        return {'FINISHED'}


class UV_OT_maya_texel_set(UVOperator, bpy.types.Operator):
    """Scale each selected shell to the texel density (around its own center)"""
    bl_idname = "uv.maya_texel_set"
    bl_label = "Set"

    def execute(self, context):
        s = settings(context)
        shells, meshes = selected_shells(context)
        if not shells:
            self.report({'WARNING'}, "Select UVs first")
            return {'CANCELLED'}
        for shell in shells:
            current = _density([shell], s.texture_size)
            if current <= 0.0:
                continue
            ob, uv, isl = shell
            loops = island_loops(isl)
            transform_loops(loops, uv, center(loops, uv), rotation(0.0) * (s.texel_density / current))
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_measure(UVOperator, bpy.types.Operator):
    """Measure the selection: shells, UV and 3D area, texel density, distance and edge length
    (Maya: Measure)"""
    bl_idname = "uv.maya_measure"
    bl_label = "Measure"

    def execute(self, context):
        s = settings(context)
        sync = is_sync(context)
        size = s.texture_size
        shells, _objects = selected_shells(context)
        points = set()
        seen_edges = set()
        edge_uv = edge_3d = 0.0
        sel_faces = []
        for ob, bm, uv, faces in edit_meshes(context):
            for f in faces:
                if face_selected(f, uv, sync):
                    sel_faces.append((ob, uv, f))
                for l in f.loops:
                    if loop_selected(l, uv, sync):
                        co = l[uv].uv
                        points.add((round(co.x, 6), round(co.y, 6)))
                    if loop_edge_selected(l, uv, sync):
                        # A shared UV edge is selected on both of its faces: count it once.
                        ends = frozenset((round(x[uv].uv.x, 6), round(x[uv].uv.y, 6))
                                         for x in (l, l.link_loop_next))
                        key = (ob.name, l.edge.index, ends)
                        if key in seen_edges:
                            continue
                        seen_edges.add(key)
                        edge_uv += (l.link_loop_next[uv].uv - l[uv].uv).length
                        edge_3d += (ob.matrix_world @ l.link_loop_next.vert.co
                                    - ob.matrix_world @ l.vert.co).length
        lines = [f"Shells: {len(shells)}   Faces: {len(sel_faces)}"]
        if sel_faces:
            a_uv = sum(uv_area(f, uv) for ob, uv, f in sel_faces)
            a_3d = sum(world_area(f, ob.matrix_world) for ob, uv, f in sel_faces)
            lines.append(f"UV Area: {a_uv * 100:.3f}% of 0-1")
            lines.append(f"3D Area: {a_3d:.4f} m²")
        if shells:
            lines.append(f"Texel Density: {_density(shells, size):.2f} px/m @ {size}")
        if len(points) == 2:
            a, b = (Vector(p) for p in points)
            d = (a - b).length
            lines.append(f"Distance: {d:.4f} UV  ({d * size:.1f} px)")
            delta = b - a
            lines.append(f"Angle: {math.degrees(math.atan2(delta.y, delta.x)):.2f}°")
        if edge_uv > 0.0:
            lines.append(f"Edge Length: {edge_uv:.4f} UV / {edge_3d:.4f} m")
        s.measure_text = "\n".join(lines)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# UV sets
# ---------------------------------------------------------------------------

_uv_set_items = []


def _uv_set_enum(self, context):
    _uv_set_items.clear()
    ob = context.edit_object or context.active_object
    if ob is not None and ob.type == 'MESH':
        active = ob.data.uv_layers.active
        for layer in ob.data.uv_layers:
            if active is None or layer.name != active.name:
                _uv_set_items.append((layer.name, layer.name, ""))
    if not _uv_set_items:
        _uv_set_items.append(('NONE', "(no other UV set)", ""))
    return _uv_set_items


class UV_OT_maya_copy_uv_set(UVOperator, bpy.types.Operator):
    """Copy the selected faces' UVs from the current UV set to another one
    (Maya: Copy UVs to UV Set)"""
    bl_idname = "uv.maya_copy_uv_set"
    bl_label = "Copy UVs to UV Set"
    bl_property = "target"

    target: bpy.props.EnumProperty(name="Target", items=_uv_set_enum)

    def invoke(self, context, event):
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.target == 'NONE':
            self.report({'WARNING'}, "Add another UV set first")
            return {'CANCELLED'}
        sync = is_sync(context)
        for ob, bm, uv, faces in edit_meshes(context):
            dst = bm.loops.layers.uv.get(self.target)
            if dst is None or dst == uv:
                continue
            for f in faces:
                if face_selected(f, uv, sync):
                    for l in f.loops:
                        l[dst].uv = l[uv].uv
            update(ob)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Tools (interactive)
# ---------------------------------------------------------------------------

TOOL_IDS = {
    'SELECT': ("builtin.select_box",),
    'MOVE': ("builtin.move",),
    'ROTATE': ("builtin.rotate",),
    'SCALE': ("builtin.scale",),
    'CUT': ("builtin.rip_region",),
    'GRAB': ("sculpt.uv_sculpt_grab", "builtin_brush.Grab"),
    'UNFOLD': ("sculpt.uv_sculpt_relax", "builtin_brush.Relax"),
    'PINCH': ("sculpt.uv_sculpt_pinch", "builtin_brush.Pinch"),
}


class UV_OT_maya_tool(bpy.types.Operator):
    """Activate a UV Editor tool (Maya: Tools)"""
    bl_idname = "uv.maya_tool"
    bl_label = "UV Tool"

    tool: bpy.props.EnumProperty(
        items=[('SELECT', "Select", ""), ('MOVE', "Move", ""), ('ROTATE', "Rotate", ""),
               ('SCALE', "Scale", ""), ('CUT', "Cut (Rip)", "Rip UVs apart by dragging"),
               ('GRAB', "Grab", "Grab brush"), ('UNFOLD', "Unfold / Optimize Brush", "Relax brush"),
               ('PINCH', "Pinch", "Pinch brush")])

    def execute(self, context):
        for idname in TOOL_IDS[self.tool]:
            try:
                bpy.ops.wm.tool_set_by_id(name=idname, space_type='IMAGE_EDITOR')
                return {'FINISHED'}
            except (RuntimeError, TypeError):
                continue
        self.report({'WARNING'}, "This tool is not available in this Blender version")
        return {'CANCELLED'}


classes = (
    UV_OT_maya_planar,
    UV_OT_maya_view_project,
    UV_OT_maya_auto_seams,
    UV_OT_maya_sew,
    UV_OT_maya_create_shell,
    UV_OT_maya_straighten_shell,
    UV_OT_maya_unfold,
    UV_OT_maya_optimize,
    UV_OT_maya_layout,
    UV_OT_maya_texel_get,
    UV_OT_maya_texel_set,
    UV_OT_maya_measure,
    UV_OT_maya_copy_uv_set,
    UV_OT_maya_tool,
)
