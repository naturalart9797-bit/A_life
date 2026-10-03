"""Generation: origins -> Physarum network -> tube mesh bound to the target."""

import random
import time
import traceback

import bpy
from mathutils import Vector

from . import binding, network
from .mesh import MeshBuilder, assign_vertex_groups, blob, build_mesh, cap, tube
from .sampler import BodySampler

SOURCE_PROP = "slime_net_source"
ORIGIN_PROP = "slime_net_origin"
GROWTH_GROUP = "SlimeNet_Growth"


def get_target(context):
    P = context.scene.slime_net
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
    return [o for o in bpy.data.objects if o.type == "EMPTY" and o.get(ORIGIN_PROP) == target.name]


def origin_reach(o):
    return o.empty_display_size * max(o.matrix_world.to_scale())


def network_objects(target):
    return [o for o in bpy.data.objects if o.get(SOURCE_PROP) == target.name]


def remove_network(target):
    for obj in network_objects(target):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)


def add_origin(context, target, location, reach):
    name = "SlimeOrigin"
    o = bpy.data.objects.new(name, None)
    o.empty_display_type = "SPHERE"
    o.empty_display_size = reach
    o[ORIGIN_PROP] = target.name
    colls = target.users_collection or (context.scene.collection,)
    colls[0].objects.link(o)
    o.parent = target
    o.matrix_parent_inverse.identity()
    o.location = target.matrix_world.inverted() @ location
    o.show_in_front = True
    o.hide_render = True
    return o


# ----------------------------------------------------------------------
def generate(report, context, target):
    P = context.scene.slime_net
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
        spacing = P.spacing * scale
        opts = []
        for o in origins:
            hit = sampler.nearest(o.matrix_world.translation)
            if hit is not None:
                opts.append((hit.loc, origin_reach(o)))
        if not opts:
            return None, "起点の近くに対象の面がありません"
        nodes = network.scatter_nodes(sampler, [p for p, _ in opts], max(r for _, r in opts), spacing, rng)
        if len(nodes) < 8:
            return None, "範囲が小さすぎます（範囲を広げるか、点の間隔を小さく）"
        edges = network.connect(sampler, nodes, spacing)

        # Geodesic range per origin; node.dist = normalised distance (0 origin .. 1 edge of range).
        norm = [float("inf")] * len(nodes)
        src_all = []
        for p, reach in opts:
            s = network.nearest_node(nodes, p)
            src_all.append(s)
            dist = network.geodesic(nodes, edges, [s])
            for i, d in enumerate(dist):
                norm[i] = min(norm[i], d / max(reach, 1e-9))
        keep = [d <= 1.0 for d in norm]
        for n, d in zip(nodes, norm):
            n.dist = d
        nodes, edges, remap = network.restrict(nodes, edges, keep)
        sources = sorted({remap[s] for s in src_all if s in remap})
        if not sources or not edges:
            return None, "ネットワークを作れませんでした（対象の面がつながっているか確認）"

        foods = _pick_foods(nodes, sources, P, rng, spacing)
        lengths = [(nodes[a].co - nodes[b].co).length for a, b in edges]
        mu = 1.3 - 0.4 * P.loopiness  # 1.3: tree-like ... 0.9: dense reticulated mesh
        D = network.physarum(len(nodes), edges, lengths, sources, foods, rng,
                             iterations=P.iterations, mu=mu, origin_bias=P.origin_bias)
        branches, node_r = network.extract_branches(nodes, edges, D, set(sources), P.keep_decades,
                                                    P.r_min * scale, P.r_max * scale)
        if not branches:
            return None, "管が残りませんでした（「細い管を残す」を増やしてください）"

        b = MeshBuilder()
        off = P.surface_offset * scale
        rmax = P.r_max * scale
        weights_cache = {}

        def node_weights(i):
            w = weights_cache.get(i)
            if w is None:
                w = weights_cache[i] = sampler.weights_at(nodes[i].hit)
            return w

        deg = {}
        for ids, _r in branches:
            for k in (ids[0], ids[-1]):
                deg[k] = deg.get(k, 0) + 1
        for ids, rads in branches:
            r_pts = [rads[0]] + [max(rads[k - 1], rads[k]) for k in range(1, len(rads))] + [rads[-1]]
            r_pts = r_pts[:len(ids)]
            pts = [nodes[i].co.copy() for i in ids]
            for _ in range(2):
                pts = [pts[0]] + [(pts[k - 1] + pts[k] * 2.0 + pts[k + 1]) * 0.25
                                  for k in range(1, len(pts) - 1)] + [pts[-1]]
            hits = [sampler.nearest(p) for p in pts]
            normals = [h.normal for h in hits]
            centers = [h.loc + h.normal * (off + r) for h, r in zip(hits, r_pts)]
            dists = [min(1.0, nodes[i].dist) for i in ids]
            thick = [min(1.0, r / rmax) for r in r_pts]
            ws = [node_weights(i) for i in ids]
            rings = tube(b, centers, normals, r_pts, ws, dists, thick, P.ring_res)
            if not rings:
                continue
            for end, ring, k in ((ids[-1], rings[-1], -1), (ids[0], rings[0], 0)):
                if deg.get(end, 0) == 1 and end not in sources:
                    t = (centers[k] - centers[k - 1 if k == -1 else 1]) * (1 if k == -1 else 1)
                    t = t.normalized() if t.length_squared > 1e-16 else normals[k]
                    cap(b, centers[k], normals[k], t, r_pts[k], ring if k == -1 else list(reversed(ring)),
                        ws[k], dists[k], thick[k])
        for i, r in node_r.items():
            if deg.get(i, 0) >= 2 or i in sources:
                h = nodes[i].hit
                rr = r * (1.6 if i in sources else 1.15)
                blob(b, h.loc + h.normal * (off + r), rr, node_weights(i), min(1.0, nodes[i].dist),
                     min(1.0, r / rmax))

        name = target.name + "_SlimeNet"
        me = build_mesh(b, name, sampler.to_local)
        obj = bpy.data.objects.new(name, me)
        obj[SOURCE_PROP] = target.name
        colls = target.users_collection or (context.scene.collection,)
        colls[0].objects.link(obj)
        binding.attach(obj, target)
        obj.data.materials.append(material(target, P))
        assign_vertex_groups(obj, b, bone_names if mode == "ARMATURE" else set())
        obj.hide_select = True
        if mode == "ARMATURE":
            binding.add_armature(obj, target)
        elif mode == "SURFACE":
            context.view_layer.update()
            binding.add_surface_deform(context, obj, target)
        add_growth(obj, P.growth)
    stats = "粘菌ネットワーク: 点 %d / 管 %d 本 / 頂点 %d (%.1f秒)" % (
        len(nodes), len(branches), len(b.verts), time.time() - t0)
    return obj, stats


def _pick_foods(nodes, sources, P, rng, spacing):
    """Food sources: spread inside the range, denser toward the front."""
    n = len(nodes)
    want = max(2, P.food_count)
    front = [i for i, nd in enumerate(nodes) if nd.dist >= 0.8]
    inner = [i for i, nd in enumerate(nodes) if nd.dist < 0.8]
    n_front = int(round(want * P.front_ratio))
    picks = []
    min_d = spacing * 3.0
    for pool, count in ((front, n_front), (inner, want - n_front)):
        pool = pool[:]
        rng.shuffle(pool)
        got = 0
        for i in pool:
            if got >= count:
                break
            if i in sources or any((nodes[i].co - nodes[j].co).length < min_d for j in picks):
                continue
            picks.append(i)
            got += 1
    return picks or [rng.randrange(n)]


# ----------------------------------------------------------------------
# Growth animation (Geometry Nodes: delete where slime_dist > growth)
# ----------------------------------------------------------------------
def _growth_group():
    ng = bpy.data.node_groups.get(GROWTH_GROUP)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GROWTH_GROUP, "GeometryNodeTree")
    if hasattr(ng, "interface"):
        ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        s = ng.interface.new_socket("成長", in_out="INPUT", socket_type="NodeSocketFloat")
        s.min_value, s.max_value, s.default_value = 0.0, 1.0, 1.0
        ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    else:  # Blender < 4.0
        ng.inputs.new("NodeSocketGeometry", "Geometry")
        s = ng.inputs.new("NodeSocketFloat", "成長")
        s.min_value, s.max_value, s.default_value = 0.0, 1.0, 1.0
        ng.outputs.new("NodeSocketGeometry", "Geometry")
    nodes, links = ng.nodes, ng.links
    gi = nodes.new("NodeGroupInput")
    go = nodes.new("NodeGroupOutput")
    attr = nodes.new("GeometryNodeInputNamedAttribute")
    attr.data_type = "FLOAT"
    attr.inputs["Name"].default_value = "slime_dist"
    cmp = nodes.new("FunctionNodeCompare")
    cmp.data_type = "FLOAT"
    cmp.operation = "GREATER_THAN"
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    links.new(attr.outputs["Attribute"], cmp.inputs[0])
    links.new(gi.outputs[1], cmp.inputs[1])
    links.new(gi.outputs[0], delete.inputs["Geometry"])
    links.new(cmp.outputs["Result"], delete.inputs["Selection"])
    links.new(delete.outputs[0], go.inputs[0])
    return ng


def add_growth(obj, value):
    mod = obj.modifiers.new("成長", "NODES")
    mod.node_group = _growth_group()
    key = growth_key(mod)
    if key:
        mod[key] = value
    return mod


def growth_mod(obj):
    return next((m for m in obj.modifiers if m.type == "NODES" and m.node_group
                 and m.node_group.name.startswith(GROWTH_GROUP)), None)


def growth_key(mod):
    ng = mod.node_group
    if hasattr(ng, "interface"):
        for item in ng.interface.items_tree:
            if getattr(item, "in_out", "") == "INPUT" and getattr(item, "socket_type", "") == "NodeSocketFloat":
                return item.identifier
    else:
        for s in ng.inputs:
            if s.type == "VALUE":
                return s.identifier
    return None


# ----------------------------------------------------------------------
def material(target, P):
    name = "SlimeNet_" + target.name
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
    attr.attribute_name = "slime_thick"
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*P.color_thin, 1.0)
    ramp.color_ramp.elements[1].color = (*P.color_thick, 1.0)
    nt.links.new(attr.outputs["Fac"], ramp.inputs["Fac"])
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 80.0
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Roughness"].default_value = P.roughness
    nt.links.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    for key in ("Subsurface Weight", "Subsurface"):
        if key in bsdf.inputs:
            bsdf.inputs[key].default_value = P.subsurface
            break
    if "Subsurface Color" in bsdf.inputs:  # Blender < 4.0
        nt.links.new(ramp.outputs["Color"], bsdf.inputs["Subsurface Color"])
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = (*P.color_thick, 1.0)
    return mat


def run(context, target, report):
    if context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    try:
        obj, info = generate(report, context, target)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        report({"ERROR"}, "生成に失敗しました: %s" % exc)
        return None
    if obj is None:
        report({"WARNING"}, info)
        return None
    report({"INFO"}, info)
    return obj
