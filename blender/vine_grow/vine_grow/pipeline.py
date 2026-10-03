"""Generation: origins -> space-colonisation vines -> tube mesh bound to the target."""

import random
import time
import traceback

import bpy

from . import binding, colonize, route, surface, tangle
from .mesh import MeshBuilder, assign_vertex_groups, blob, build_mesh, cap, tube
from .sampler import BodySampler

SOURCE_PROP = "vine_grow_source"
ORIGIN_PROP = "vine_grow_origin"
GROWTH_GROUP = "VineGrow_Growth"
GROWTH_NODE = "growth"


def get_target(context):
    P = context.scene.vine_grow
    if P.target is not None:
        return P.target
    obj = context.active_object
    if obj and obj.type == "MESH" and not obj.get(SOURCE_PROP):
        return obj
    return None


def target_scale(target, P):
    if not P.auto_scale:
        return 1.0
    d = max(target.dimensions)
    return d / 1.7 if d > 1e-6 else 1.0


def origin_objects(target):
    objs = [o for o in bpy.data.objects if o.type == "EMPTY" and o.get(ORIGIN_PROP) == target.name]
    return sorted(objs, key=lambda o: (o.get("vine_grow_order", 0), o.name))


def origin_reach(o):
    r = getattr(o, "vine_grow_reach", 0.0)
    if r > 0.0:
        return r
    return o.empty_display_size * max(o.matrix_world.to_scale())


def sync_display(o):
    sc = max(o.matrix_world.to_scale()) or 1.0
    o.empty_display_size = max(o.vine_grow_reach / sc, 1e-6)


def origin_density(o, P):
    d = getattr(o, "vine_grow_density", -1.0)
    return P.tangle_density if d < 0.0 else d


DENSITY_DISPLAY = 0.02  # sphere radius (m) of a point at 10 vines / 100 cm^2


def sync_density_display(o):
    """In density mode the sphere size shows the density (cube root, so 8x denser = 2x bigger)."""
    if o.vine_grow_density < 0.0:
        return
    sc = max(o.matrix_world.to_scale()) or 1.0
    size = DENSITY_DISPLAY
    if o.parent is not None and max(o.parent.dimensions) > 1e-6:
        size *= max(o.parent.dimensions) / 1.7
    o.empty_display_size = max(size * (max(o.vine_grow_density, 0.05) / 10.0) ** (1.0 / 3.0) / sc, 1e-6)


def network_objects(target):
    return [o for o in bpy.data.objects if o.get(SOURCE_PROP) == target.name]


def remove_network(target):
    for obj in network_objects(target):
        data = obj.data
        groups = [m.node_group for m in obj.modifiers if m.type == "NODES" and m.node_group]
        bpy.data.objects.remove(obj, do_unlink=True)
        for g in groups:
            if g.users == 0:
                bpy.data.node_groups.remove(g)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)


def add_origin(context, target, location, reach):
    o = bpy.data.objects.new("VineOrigin", None)
    o.empty_display_type = "SPHERE"
    o[ORIGIN_PROP] = target.name
    colls = target.users_collection or (context.scene.collection,)
    colls[0].objects.link(o)
    o.parent = target
    o.matrix_parent_inverse.identity()
    o.location = target.matrix_world.inverted() @ location
    o.show_in_front = True
    o.hide_render = True
    o.matrix_world = target.matrix_world @ o.matrix_basis
    o.vine_grow_reach = reach
    if context.scene.vine_grow.mode == "DENSITY":
        o.vine_grow_density = context.scene.vine_grow.tangle_density
    others = [x.get("vine_grow_order", 0) for x in origin_objects(target) if x != o]
    o["vine_grow_order"] = (max(others) + 1) if others else 0
    return o


# ----------------------------------------------------------------------
def generate(report, context, target):
    P = context.scene.vine_grow
    origins = origin_objects(target)
    if not origins:
        return None, "起点がありません。「起点を置く」で対象の上をクリックしてください"
    remove_network(target)
    mode = P.bind_mode
    if mode == "ARMATURE" and not binding.target_armatures(target):
        mode = "SURFACE" if target.data.shape_keys else "NONE"
    t0 = time.time()
    with binding.rest_pose(context, target, P.rest_pose):
        bone_names = binding.bone_names_of(target) if mode == "ARMATURE" else None
        sampler = BodySampler(context, target, "", bone_names)
        scale = target_scale(target, P)
        rng = random.Random(P.seed)
        spacing = P.attractor_spacing * scale
        opts = []
        for o in origins:
            hit = sampler.nearest(o.matrix_world.translation)
            if hit is not None:
                opts.append((hit, origin_density(o, P) if P.mode == "DENSITY" else origin_reach(o)))
        if not opts:
            return None, "起点の近くに対象の面がありません"

        push = colonize.Pusher(sampler, P.clearance * scale)
        tree = colonize.Tree()
        if P.mode == "DENSITY":
            if not any(v > 0.0 for _, v in opts):
                return None, "密度が0より大きい点がありません"
            field = tangle.Field(sampler, opts, P, scale, rng)
            if not field.ok():
                return None, "点の近くに対象の面がありません。「密度の広がり」を大きくしてみてください"
            radii = tangle.grow(tree, sampler, field, P, scale, push, rng)
            if len(tree.pos) < 2:
                return None, "つるが伸びませんでした（密度・長さを確認）"
        elif P.mode == "ROUTE":
            if len(opts) < 2:
                return None, "経路モードでは起点（経由点）が2つ以上必要です"
            rt = route.build_route(sampler, [h for h, _ in opts], [r for _, r in opts], P, scale, rng)
            if rt is None or len(rt[0]) < 2:
                return None, "経路を作れませんでした"
            radii = route.grow_strands(tree, rt, P, scale, push, rng, sampler)
            route.add_shoots(tree, radii, P, scale, push, rng)
            route.add_aerial_route(tree, radii, P, scale, push, rng)
            route.add_tip_tendrils(tree, radii, P, scale, push, rng)
            if len(tree.pos) < 2:
                return None, "つるが伸びませんでした"
        else:
            # Surface anchors within each origin's range (geodesic, along the body).
            anchors = surface.scatter_nodes(sampler, [h.loc for h, _ in opts], max(r for _, r in opts), spacing, rng)
            if len(anchors) < 4:
                return None, ("範囲が小さすぎます（範囲 %.3g / 密度の間隔 %.3g）。範囲を広げるか、間隔を小さく"
                              % (max(r for _, r in opts), spacing))
            edges = surface.connect(sampler, anchors, spacing)
            norm = [float("inf")] * len(anchors)
            for hit, reach in opts:
                s = surface.nearest_node(anchors, hit.loc)
                for i, d in enumerate(surface.geodesic(anchors, edges, [s])):
                    norm[i] = min(norm[i], d / max(reach, 1e-9))
            anchors = [a for a, d in zip(anchors, norm) if d <= 1.0]

            attractors = colonize.make_attractors(anchors, P, scale, rng)
            for hit, _r in opts:
                p, n = push(hit.loc + hit.normal * (P.clearance * scale))
                tree.add(p, -1, hit.normal.copy(), n, 0, rng.uniform(0.0, 6.283))
            colonize.colonize(tree, attractors, P, scale, push, rng, P.max_nodes)
            tree = colonize.prune(tree, P.min_twig * scale)
            colonize.add_aerial(tree, P, scale, push, rng)
            colonize.add_tendrils(tree, P, scale, push, rng)
            if len(tree.pos) <= len(opts):
                return None, "つるが伸びませんでした（範囲・密度・影響距離を確認）"

            radii = colonize.pipe_radii(tree, P.r_min * scale, P.r_max * scale, P.pipe_exponent)
        plen = colonize.path_lengths(tree)
        maxlen = max(plen) or 1.0
        rmax = (P.fine_r_max if P.mode == "DENSITY" else P.r_max) * scale

        weights_cache = {}

        def node_weights(i):
            w = weights_cache.get(i)
            if w is None:
                hit = sampler.nearest(tree.pos[i])
                w = weights_cache[i] = sampler.weights_at(hit) if hit else {}
            return w

        b = MeshBuilder()
        ch = tree.children()
        for seq in colonize.chains(tree):
            pts = [tree.pos[i].copy() for i in seq]
            for _ in range(2):  # smooth, then keep it outside the body
                pts = [pts[0]] + [(pts[k - 1] + pts[k] * 2.0 + pts[k + 1]) * 0.25
                                  for k in range(1, len(pts) - 1)] + [pts[-1]]
            pts = [push(p)[0] for p in pts]
            normals = [tree.normal[i] for i in seq]
            rs = [radii[i] for i in seq]
            # The child branch starts inside its parent's tube; give it the child's radius there.
            rs[0] = min(rs[0], rs[1] * 1.15) if len(rs) > 1 else rs[0]
            dists = [min(1.0, plen[i] / maxlen) for i in seq]
            thick = [min(1.0, r / rmax) for r in rs]
            ws = [node_weights(i) for i in seq]
            rings = tube(b, pts, normals, rs, ws, dists, thick, P.ring_res)
            if rings and tree.parent[seq[0]] < 0 and P.mode != "RADIAL":  # open base of a vine
                t = pts[1] - pts[0]
                t = t.normalized() if t.length_squared > 1e-16 else normals[0]
                cap(b, pts[0], normals[0], -t, rs[0], rings[0], ws[0], dists[0], thick[0], flip=True)
            if rings and not ch[seq[-1]]:
                t = pts[-1] - pts[-2]
                t = t.normalized() if t.length_squared > 1e-16 else normals[-1]
                cap(b, pts[-1], normals[-1], t, rs[-1], rings[-1], ws[-1], dists[-1], thick[-1])
        for i, kids in enumerate(ch):
            if len(kids) >= 2 or (tree.parent[i] < 0 and P.mode == "RADIAL"):
                blob(b, tree.pos[i], radii[i] * (1.3 if tree.parent[i] < 0 else 1.05), node_weights(i),
                     min(1.0, plen[i] / maxlen), min(1.0, radii[i] / rmax), seg=6, rings=4)

        name = target.name + "_Vines"
        me = build_mesh(b, name, sampler.to_local)
        obj = bpy.data.objects.new(name, me)
        obj[SOURCE_PROP] = target.name
        colls = target.users_collection or (context.scene.collection,)
        colls[0].objects.link(obj)
        binding.attach(obj, target)
        try:
            obj.data.materials.append(material(target, P))
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            report({"WARNING"}, "マテリアルの設定に失敗しました: %s" % exc)
        assign_vertex_groups(obj, b, bone_names if mode == "ARMATURE" else set())
        obj.hide_select = True
        if mode == "ARMATURE":
            binding.add_armature(obj, target)
        elif mode == "SURFACE":
            context.view_layer.update()
            binding.add_surface_deform(context, obj, target)
        try:
            add_growth(obj, P.growth)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            report({"WARNING"}, "成長アニメーションの設定に失敗しました: %s" % exc)
    stats = "つる: 節 %d / 枝 %d 本 / 頂点 %d (%.1f秒)" % (
        len(tree.pos), len(colonize.chains(tree)), len(b.verts), time.time() - t0)
    return obj, stats


# ----------------------------------------------------------------------
# Growth animation (value lives in a Value node: works in Blender 3.6 - 5.x)
# ----------------------------------------------------------------------
def _new_growth_group(name, value):
    ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    if hasattr(ng, "interface"):
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    else:
        ng.inputs.new("NodeSocketGeometry", "Geometry")
        ng.outputs.new("NodeSocketGeometry", "Geometry")
    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    val = nodes.new("ShaderNodeValue")
    val.name = val.label = GROWTH_NODE
    val.outputs[0].default_value = value
    attr = nodes.new("GeometryNodeInputNamedAttribute")
    attr.data_type = "FLOAT"
    attr.inputs["Name"].default_value = "vine_dist"
    cmp = nodes.new("FunctionNodeCompare")
    cmp.data_type = "FLOAT"
    cmp.operation = "GREATER_THAN"
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    links.new(attr.outputs["Attribute"], cmp.inputs[0])
    links.new(val.outputs[0], cmp.inputs[1])
    links.new(gi.outputs[0], delete.inputs["Geometry"])
    links.new(cmp.outputs["Result"], delete.inputs["Selection"])
    links.new(delete.outputs[0], go.inputs[0])
    return ng


def add_growth(obj, value):
    mod = obj.modifiers.new("成長", "NODES")
    mod.node_group = _new_growth_group("%s_%s" % (GROWTH_GROUP, obj.name), value)
    return mod


def growth_mod(obj):
    return next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                 and m.node_group.name.startswith(GROWTH_GROUP)), None)


def growth_node(obj):
    mod = growth_mod(obj)
    return mod.node_group.nodes.get(GROWTH_NODE) if mod else None


# ----------------------------------------------------------------------
def material(target, P):
    name = "VineGrow_" + target.name
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True
    except (AttributeError, TypeError):
        pass
    nt = mat.node_tree
    if nt is None:
        return mat
    nt.nodes.clear()
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "vine_thick"
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*P.color_thin, 1.0)
    ramp.color_ramp.elements[1].color = (*P.color_thick, 1.0)
    nt.links.new(attr.outputs["Fac"], ramp.inputs["Fac"])
    tc = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 60.0
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = P.bump
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = P.roughness
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    for key in ("Subsurface Weight", "Subsurface"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = P.subsurface
            break
    if "Subsurface Color" in bsdf.inputs:
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Subsurface Color"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = (*P.color_thick, 1.0)
    return mat


def run(context, target, report):
    P = context.scene.vine_grow
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        obj, info = generate(report, context, target)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        P.last_message, P.last_ok = "生成に失敗しました: %s" % exc, False
        report({"ERROR"}, P.last_message)
        return None
    P.last_message, P.last_ok = info, obj is not None
    report({"INFO"} if obj is not None else {"WARNING"}, info)
    return obj
