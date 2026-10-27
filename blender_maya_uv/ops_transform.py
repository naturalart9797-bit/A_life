# Transform section of the UV Toolkit: move / rotate / scale / flip, align,
# snap to tile, normalize, unitize, distribute, gather and stack.

import math

import bpy
from mathutils import Matrix, Vector

from .common import (
    UVOperator, EPS, settings, update_all, is_sync, edit_meshes, update, loop_groups, group_bounds,
    transform_selection, selected_shells, islands, island_loops, bounds, center, uv_area,
    world_area, move_loops, transform_loops, rotation, aspect_rotation, image_aspect,
    face_selected, op_available, loop_selected, loop_edge_selected,
)


class UV_OT_maya_move(UVOperator, bpy.types.Operator):
    """Move the selected UVs by the step amount"""
    bl_idname = "uv.maya_move"
    bl_label = "Move UVs"

    direction: bpy.props.EnumProperty(
        items=[('UP', "Up", ""), ('DOWN', "Down", ""), ('LEFT', "Left", ""), ('RIGHT', "Right", "")])

    def execute(self, context):
        step = settings(context).move_step
        offset = Vector({'UP': (0, step), 'DOWN': (0, -step),
                         'LEFT': (-step, 0), 'RIGHT': (step, 0)}[self.direction])
        groups, meshes = loop_groups(context, per_shell=False)
        for group in groups:
            for uv, loops in group:
                move_loops(loops, uv, offset)
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_rotate(UVOperator, bpy.types.Operator):
    """Rotate the selected UVs around the pivot"""
    bl_idname = "uv.maya_rotate"
    bl_label = "Rotate UVs"

    angle: bpy.props.FloatProperty(name="Angle", subtype='ANGLE', default=math.radians(90.0))

    def execute(self, context):
        transform_selection(context, aspect_rotation(context, self.angle))
        return {'FINISHED'}


class UV_OT_maya_scale(UVOperator, bpy.types.Operator):
    """Scale the selected UVs around the pivot"""
    bl_idname = "uv.maya_scale"
    bl_label = "Scale UVs"

    factor: bpy.props.FloatProperty(name="Factor", default=2.0)
    axis: bpy.props.EnumProperty(items=[('UV', "UV", ""), ('U', "U", ""), ('V', "V", "")])

    def execute(self, context):
        fu = self.factor if self.axis in {'UV', 'U'} else 1.0
        fv = self.factor if self.axis in {'UV', 'V'} else 1.0
        transform_selection(context, Matrix(((fu, 0), (0, fv))))
        return {'FINISHED'}


class UV_OT_maya_flip(UVOperator, bpy.types.Operator):
    """Flip the selected UVs in U or V around the pivot"""
    bl_idname = "uv.maya_flip"
    bl_label = "Flip UVs"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])

    def execute(self, context):
        mat2 = Matrix(((-1, 0), (0, 1))) if self.axis == 'U' else Matrix(((1, 0), (0, -1)))
        transform_selection(context, mat2)
        return {'FINISHED'}


class UV_OT_maya_align(UVOperator, bpy.types.Operator):
    """Align the selected UVs (or shells, with the Shell pivot) to the min / average / max"""
    bl_idname = "uv.maya_align"
    bl_label = "Align UVs"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])
    mode: bpy.props.EnumProperty(items=[('MIN', "Min", ""), ('MEAN', "Average", ""), ('MAX', "Max", "")])

    def execute(self, context):
        i = 0 if self.axis == 'U' else 1
        if settings(context).pivot == 'SHELL':
            return self._align_shells(context, i)
        groups, meshes = loop_groups(context, per_shell=False)
        for group in groups:
            values = [l[uv].uv[i] for uv, loops in group for l in loops]
            target = self._target(values)
            for uv, loops in group:
                for l in loops:
                    co = l[uv].uv.copy()
                    co[i] = target
                    l[uv].uv = co
        update_all(meshes)
        return {'FINISHED'}

    def _target(self, values):
        if self.mode == 'MIN':
            return min(values)
        if self.mode == 'MAX':
            return max(values)
        return sum(values) / len(values)

    def _align_shells(self, context, i):
        # Maya "Align shells": move whole shells so their bounds line up.
        shells, meshes = selected_shells(context)
        boxes = [(uv, isl, bounds(island_loops(isl), uv)) for ob, uv, isl in shells]
        if not boxes:
            return {'CANCELLED'}
        if self.mode == 'MIN':
            target = min(lo[i] for _, _, (lo, hi) in boxes)
        elif self.mode == 'MAX':
            target = max(hi[i] for _, _, (lo, hi) in boxes)
        else:
            target = sum((lo[i] + hi[i]) * 0.5 for _, _, (lo, hi) in boxes) / len(boxes)
        for uv, isl, (lo, hi) in boxes:
            ref = lo[i] if self.mode == 'MIN' else hi[i] if self.mode == 'MAX' else (lo[i] + hi[i]) * 0.5
            offset = Vector((0.0, 0.0))
            offset[i] = target - ref
            move_loops(island_loops(isl), uv, offset)
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_snap_tile(UVOperator, bpy.types.Operator):
    """Snap the selection (or each shell, with the Shell pivot) to a corner, edge or the
    center of its UV tile (Maya: Snap)"""
    bl_idname = "uv.maya_snap_tile"
    bl_label = "Snap to Tile"

    u: bpy.props.FloatProperty(default=0.0)
    v: bpy.props.FloatProperty(default=0.0)

    def execute(self, context):
        per_shell = settings(context).pivot == 'SHELL'
        groups, meshes = loop_groups(context, per_shell, whole_shells=per_shell)
        for group in groups:
            lo, hi = group_bounds(group)
            tile = Vector((math.floor((lo.x + hi.x) * 0.5), math.floor((lo.y + hi.y) * 0.5)))
            ref = Vector((lo.x + (hi.x - lo.x) * self.u, lo.y + (hi.y - lo.y) * self.v))
            offset = tile + Vector((self.u, self.v)) - ref
            for uv, loops in group:
                move_loops(loops, uv, offset)
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_normalize(UVOperator, bpy.types.Operator):
    """Fit the selection (or each shell, with the Shell pivot) into the 0-1 tile"""
    bl_idname = "uv.maya_normalize"
    bl_label = "Normalize"

    def execute(self, context):
        s = settings(context)
        per_shell = s.pivot == 'SHELL'
        groups, meshes = loop_groups(context, per_shell, whole_shells=True)
        for group in groups:
            lo, hi = group_bounds(group)
            size = hi - lo
            if s.normalize_keep_aspect:
                k = max(size.x, size.y)
                scale = Vector((k, k))
            else:
                scale = size
            scale = Vector((scale.x or 1.0, scale.y or 1.0))
            for uv, loops in group:
                for l in loops:
                    p = l[uv].uv - lo
                    l[uv].uv = Vector((p.x / scale.x, p.y / scale.y))
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_unitize(UVOperator, bpy.types.Operator):
    """Make every selected face fill the whole 0-1 tile (Maya: Unitize)"""
    bl_idname = "uv.maya_unitize"
    bl_label = "Unitize"

    def execute(self, context):
        sync = is_sync(context)
        corners = (Vector((0, 0)), Vector((1, 0)), Vector((1, 1)), Vector((0, 1)))
        for ob, bm, uv, faces in edit_meshes(context):
            for f in faces:
                if not face_selected(f, uv, sync):
                    continue
                if len(f.loops) == 4:
                    for l, c in zip(f.loops, corners):
                        l[uv].uv = c
                    continue
                rot = f.normal.to_track_quat('Z', 'Y').to_matrix().inverted()
                pts = [(rot @ l.vert.co).to_2d() for l in f.loops]
                lo = Vector((min(p.x for p in pts), min(p.y for p in pts)))
                hi = Vector((max(p.x for p in pts), max(p.y for p in pts)))
                size = hi - lo
                for l, p in zip(f.loops, pts):
                    q = p - lo
                    l[uv].uv = Vector((q.x / (size.x or 1.0), q.y / (size.y or 1.0)))
            update(ob)
        return {'FINISHED'}


class UV_OT_maya_distribute(UVOperator, bpy.types.Operator):
    """Space the selected shells in U or V. With Spacing 0 they spread evenly between the
    first and last shell, otherwise they are placed Spacing apart"""
    bl_idname = "uv.maya_distribute"
    bl_label = "Distribute Shells"

    axis: bpy.props.EnumProperty(items=[('U', "U", ""), ('V', "V", "")])

    def execute(self, context):
        i = 0 if self.axis == 'U' else 1
        spacing = settings(context).distribute_spacing
        shells, meshes = selected_shells(context)
        if len(shells) < (2 if spacing > 0 else 3):
            self.report({'WARNING'}, "Select more shells")
            return {'CANCELLED'}
        info = []
        for ob, uv, isl in shells:
            lo, hi = bounds(island_loops(isl), uv)
            info.append((lo[i], hi[i] - lo[i], uv, isl))
        info.sort(key=lambda t: t[0])
        start = info[0][0]
        if spacing > 0:
            gap = spacing
        else:
            end = info[-1][0] + info[-1][1]
            gap = (end - start - sum(t[1] for t in info)) / (len(info) - 1)
        pos = start
        for lo, size, uv, isl in info:
            offset = Vector((0.0, 0.0))
            offset[i] = pos - lo
            move_loops(island_loops(isl), uv, offset)
            pos += size + gap
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_gather(UVOperator, bpy.types.Operator):
    """Bring the selected shells (all shells if none are selected) back into the 0-1 tile,
    keeping their position inside the tile (Maya: Gather Shells)"""
    bl_idname = "uv.maya_gather"
    bl_label = "Gather Shells"

    def execute(self, context):
        shells, meshes = selected_shells(context)
        if not shells:
            meshes = edit_meshes(context)
            shells = [(ob, uv, isl) for ob, bm, uv, faces in meshes for isl in islands(faces, uv)]
        for ob, uv, isl in shells:
            loops = island_loops(isl)
            c = center(loops, uv)
            move_loops(loops, uv, -Vector((math.floor(c.x), math.floor(c.y))))
        update_all(meshes)
        return {'FINISHED'}


def _stack(shells):
    target = max(shells, key=lambda s: sum(uv_area(f, s[1]) for f in s[2]))
    dst = center(island_loops(target[2]), target[1])
    for ob, uv, isl in shells:
        loops = island_loops(isl)
        move_loops(loops, uv, dst - center(loops, uv))


class UV_OT_maya_stack(UVOperator, bpy.types.Operator):
    """Stack the selected shells on top of the largest one"""
    bl_idname = "uv.maya_stack"
    bl_label = "Stack Shells"

    orient: bpy.props.BoolProperty(name="Orient", description="Orient the shells first",
                                   default=False)

    def execute(self, context):
        if self.orient and op_available("uv.align_rotation"):
            bpy.ops.uv.align_rotation(method='AUTO')
        shells, meshes = selected_shells(context)
        if len(shells) < 2:
            self.report({'WARNING'}, "Select at least 2 shells")
            return {'CANCELLED'}
        _stack(shells)
        update_all(meshes)
        return {'FINISHED'}


class UV_OT_maya_stack_similar(UVOperator, bpy.types.Operator):
    """Stack shells that have the same topology and size (Maya: Stack Similar)"""
    bl_idname = "uv.maya_stack_similar"
    bl_label = "Stack Similar"

    def execute(self, context):
        shells, meshes = selected_shells(context)
        groups = {}
        for shell in shells:
            ob, uv, isl = shell
            area = sum(world_area(f, ob.matrix_world) for f in isl)
            key = (len(isl), sum(len(f.verts) for f in isl), _round_sig(area, 3))
            groups.setdefault(key, []).append(shell)
        stacked = 0
        for group in groups.values():
            if len(group) > 1:
                _stack(group)
                stacked += len(group)
        update_all(meshes)
        if not stacked:
            self.report({'INFO'}, "No similar shells found")
        return {'FINISHED'}


def _round_sig(x, digits):
    if x <= 0.0:
        return 0.0
    return round(x, digits - 1 - int(math.floor(math.log10(x))))


class UV_OT_maya_orient_shells(UVOperator, bpy.types.Operator):
    """Rotate each selected shell so its longest straight edges line up with U or V"""
    bl_idname = "uv.maya_orient_shells"
    bl_label = "Orient Shells"

    def execute(self, context):
        if op_available("uv.align_rotation"):
            bpy.ops.uv.align_rotation(method='AUTO')
            return {'FINISHED'}
        # Older Blender: rotate by the dominant edge direction, mod 90 degrees.
        shells, meshes = selected_shells(context)
        for ob, uv, isl in shells:
            hist = {}
            for f in isl:
                for l in f.loops:
                    d = l.link_loop_next[uv].uv - l[uv].uv
                    if d.length_squared < EPS:
                        continue
                    a = math.atan2(d.y, d.x) % (math.pi / 2)
                    key = round(a, 2)
                    hist[key] = hist.get(key, 0.0) + d.length
            if not hist:
                continue
            a = max(hist, key=hist.get)
            if a > math.pi / 4:
                a -= math.pi / 2
            loops = island_loops(isl)
            transform_loops(loops, uv, center(loops, uv), rotation(-a))
        update_all(meshes)
        return {'FINISHED'}


def _wrap_half_turn(a):
    """Wrap an angle to (-90, 90] degrees: an edge has no direction."""
    while a > math.pi / 2:
        a -= math.pi
    while a <= -math.pi / 2:
        a += math.pi
    return a


def _edge_direction(isl, uv, sync, aspect):
    """Direction of the selected edges of a shell (in image pixel proportions)."""
    ax, ay = aspect
    vecs = []
    for f in isl:
        for l in f.loops:
            if loop_edge_selected(l, uv, sync):
                d = l.link_loop_next[uv].uv - l[uv].uv
                vecs.append(Vector((d.x * ax, d.y * ay)))
    if not vecs:
        # No edge: use the two selected UVs that are farthest apart.
        pts = {(round(l[uv].uv.x, 6), round(l[uv].uv.y, 6))
               for f in isl for l in f.loops if loop_selected(l, uv, sync)}
        pts = [Vector((x * ax, y * ay)) for x, y in pts]
        if len(pts) < 2:
            return None
        a, b = max(((p, q) for i, p in enumerate(pts) for q in pts[i + 1:]),
                   key=lambda pq: (pq[0] - pq[1]).length_squared)
        return b - a
    ref = max(vecs, key=lambda v: v.length_squared)
    total = Vector((0.0, 0.0))
    for v in vecs:
        total += v if v.dot(ref) >= 0 else -v
    return total if total.length_squared > EPS * EPS else None


class UV_OT_maya_straighten(UVOperator, bpy.types.Operator):
    """Straighten the selected UV edges: rows of nearly horizontal edges become horizontal
    (U), columns of nearly vertical edges become vertical (V) (Maya: Straighten UVs)"""
    bl_idname = "uv.maya_straighten"
    bl_label = "Straighten UVs"

    axis: bpy.props.EnumProperty(
        items=[('UV', "UV", "Straighten both"), ('U', "U", "Make nearly horizontal edges horizontal"),
               ('V', "V", "Make nearly vertical edges vertical")])

    def execute(self, context):
        sync = is_sync(context)
        tol = settings(context).straighten_angle
        ax, ay = image_aspect(context)
        count = 0
        for ob, bm, uv, faces in edit_meshes(context):
            # A UV vertex is a mesh vertex at one UV position (shared by its loops).
            def node(l):
                co = l[uv].uv
                return (l.vert.index, round(co.x, 5), round(co.y, 5))

            loops_of = {}
            for f in faces:
                for l in f.loops:
                    loops_of.setdefault(node(l), []).append(l)
            edges = []
            for f in faces:
                for l in f.loops:
                    nxt = l.link_loop_next
                    if loop_edge_selected(l, uv, sync) or (
                            loop_selected(l, uv, sync) and loop_selected(nxt, uv, sync)):
                        d = nxt[uv].uv - l[uv].uv
                        if d.length_squared > EPS * EPS:
                            edges.append((node(l), node(nxt), math.atan2(d.y * ay, d.x * ax)))
            for axis in (('U', 'V') if self.axis == 'UV' else (self.axis,)):
                # U: average V along rows of near-horizontal edges; V: average U along columns.
                i = 1 if axis == 'U' else 0
                parent = {}

                def find(n):
                    parent.setdefault(n, n)
                    while parent[n] != n:
                        parent[n] = parent[parent[n]]
                        n = parent[n]
                    return n

                for a, b, angle in edges:
                    off = abs(math.sin(angle)) if axis == 'U' else abs(math.cos(angle))
                    if off <= math.sin(tol) + 1e-9:
                        parent[find(a)] = find(b)
                groups = {}
                for n in list(parent):
                    groups.setdefault(find(n), []).append(n)
                for members in groups.values():
                    if len(members) < 2:
                        continue
                    loops = [l for n in members for l in loops_of.get(n, ())]
                    value = sum(l[uv].uv[i] for l in loops) / len(loops)
                    for l in loops:
                        co = l[uv].uv.copy()
                        co[i] = value
                        l[uv].uv = co
                    count += 1
            update(ob)
        if not count:
            self.report({'WARNING'}, "No selected edges within the angle")
        return {'FINISHED'}


class UV_OT_maya_orient_edge(UVOperator, bpy.types.Operator):
    """Rotate each shell so its selected edge(s) become horizontal (U) or vertical (V)
    (Maya: Orient to Edges / Rotate to U / V)"""
    bl_idname = "uv.maya_orient_edge"
    bl_label = "Orient Edge"

    axis: bpy.props.EnumProperty(
        items=[('U', "Horizontal (U)", "Make the selected edge horizontal"),
               ('V', "Vertical (V)", "Make the selected edge vertical"),
               ('AUTO', "Nearest", "Rotate to whichever of U / V is closer")],
        default='AUTO')

    def execute(self, context):
        sync = is_sync(context)
        aspect = image_aspect(context)
        shells, meshes = selected_shells(context)
        done = 0
        for ob, uv, isl in shells:
            d = _edge_direction(isl, uv, sync, aspect)
            if d is None:
                continue
            angle = math.atan2(d.y, d.x)
            to_u = _wrap_half_turn(-angle)
            to_v = _wrap_half_turn(math.pi / 2 - angle)
            if self.axis == 'U':
                delta = to_u
            elif self.axis == 'V':
                delta = to_v
            else:
                delta = to_u if abs(to_u) <= abs(to_v) else to_v
            loops = island_loops(isl)
            transform_loops(loops, uv, center(loops, uv), aspect_rotation(context, delta))
            done += 1
        update_all(meshes)
        if not done:
            self.report({'WARNING'}, "Select an edge (or two UVs) on the shells to orient")
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (
    UV_OT_maya_straighten,
    UV_OT_maya_orient_edge,
    UV_OT_maya_move,
    UV_OT_maya_rotate,
    UV_OT_maya_scale,
    UV_OT_maya_flip,
    UV_OT_maya_align,
    UV_OT_maya_snap_tile,
    UV_OT_maya_normalize,
    UV_OT_maya_unitize,
    UV_OT_maya_distribute,
    UV_OT_maya_gather,
    UV_OT_maya_stack,
    UV_OT_maya_stack_similar,
    UV_OT_maya_orient_shells,
)
