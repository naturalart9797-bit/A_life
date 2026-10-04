"""Shared generation pipeline: auto guides and node-graph driven vine building."""

import random

import bpy

from . import binding, guides
from .build import paths_from_batch
from .growth import finalize_body_paths, grow_body_vines
from .meshgen import MeshBuilder, add_leaves, add_tube, assign_vertex_groups, build_mesh
from .sampler import BodySampler, SubsetView, smoothstep
from .skirt import SkirtField, SkirtShape, build_skirt


def get_body(context):
    P = context.scene.vine_dress
    body = P.body
    if body is None:
        obj = context.active_object
        if obj and obj.type == "MESH" and not obj.get("vine_dress_source"):
            body = obj
    return body


def vine_objects(body_name):
    return [o for o in bpy.data.objects if o.get("vine_dress_source") == body_name]


def remove_generated(body_name):
    for obj in vine_objects(body_name):
        data = obj.data
        tex = [m.texture for m in getattr(obj, "modifiers", []) if m.type == "DISPLACE" and m.texture]
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
        for t in tex:
            if t.users == 0:
                bpy.data.textures.remove(t)


def sway_weight(z, water_z, H, kind, t):
    depth = water_z - z
    if depth <= 0.0:
        return 0.0
    w = smoothstep(depth / (0.15 * H))
    if kind == "skirt":
        return w * (0.2 + 0.8 * max(0.0, min(1.0, t or 0.0)))
    return w * 0.3


class Levels:
    """Heights in world space derived from the settings and the body size."""

    def __init__(self, P, sampler):
        H = sampler.height
        zmin = sampler.zmin
        self.H = H
        self.scale = H / 1.7 if P.auto_scale else 1.0
        if P.water_object:
            self.water_z = P.water_object.matrix_world.translation.z
        else:
            self.water_z = zmin + P.water_level * H
        skirt_z = self.water_z if P.skirt_at_water else zmin + P.skirt_top * H
        self.skirt_z = min(skirt_z, sampler.zmax)
        self.skirt_bot = zmin + P.skirt_bottom * H
        self.z_hi = zmin + P.cover_top * H
        self.z_lo = self.skirt_z - 0.01 * H if P.use_skirt else zmin - H


def body_scale(body, P):
    if not P.auto_scale:
        return 1.0
    mw = body.matrix_world
    zs = [(mw @ v.co).z for v in body.data.vertices]
    return (max(zs) - min(zs)) / 1.7 if zs else 1.0


def guide_hover(P, scale):
    """Guides float just above the skin so they stay visible while grooming."""
    return (P.surface_offset + P.radius * 2.0) * scale


def skirt_view(report, sampler, body, P):
    if P.skirt_body_group:
        sub = SubsetView(sampler, body, P.skirt_body_group)
        if sub.valid:
            return sub
        report({"WARNING"}, "スカート追従グループが空のため全身を使用します")
    return sampler


# ======================================================================
# Auto guides
# ======================================================================
def make_guides(report, context, P, body):
    from . import groom_tree

    count = 0
    made = []
    with binding.rest_pose(context, body, P.rest_pose):
        sampler = BodySampler(context, body, P.mask_group)
        lv = Levels(P, sampler)
        rng = random.Random(P.seed)
        spacing = P.guide_point_spacing * lv.scale

        if P.gen_body_guides and lv.z_hi > lv.z_lo:
            paths = grow_body_vines(sampler, P, rng, lv.scale, lv.z_lo, lv.z_hi)
            finalize_body_paths(paths, sampler, P)
            hover = guide_hover(P, lv.scale)
            for p in paths:
                p.points = [pt + n * hover for pt, n in zip(p.points, p.normals)]
            obj = guides.auto_group(context, body, guides.KIND_BODY, "VG_Body")
            guides.write_paths(obj, paths, spacing)
            count += len(obj.data.splines)
            made.append(obj)

        if P.gen_skirt_guides and P.use_skirt:
            view = skirt_view(report, sampler, body, P)
            paths = build_skirt(sampler, view, P, rng, lv.scale, lv.skirt_z, lv.skirt_bot)
            obj = guides.auto_group(context, body, guides.KIND_SKIRT, "VG_Skirt")
            guides.write_paths(obj, paths, spacing)
            count += len(obj.data.splines)
            made.append(obj)

    tree = groom_tree.ensure_tree(context, body)
    for obj in made:
        groom_tree.tree_add_group(tree, obj)
    guides.redraw()
    return count


# ======================================================================
# Build vines from the groom node tree
# ======================================================================
class EvalContext:
    def __init__(self, report, context, P, body, sampler, lv, out):
        self.report = report
        self.context = context
        self.P = P
        self.body = body
        self.sampler = sampler
        self.levels = lv
        self.scale = lv.scale
        self.step = out.step_length * lv.scale
        self.out = out
        self._field = False

    @property
    def field(self):
        if self._field is False:
            lv = self.levels
            self._field = None
            if lv.skirt_z - lv.skirt_bot > 1e-4:
                shape = SkirtShape(self.sampler, skirt_view(self.report, self.sampler, self.body, self.P),
                                   self.P, self.scale, lv.skirt_z, lv.skirt_bot)
                self._field = SkirtField(shape, self.out)
        return self._field


def build_vines(report, context, P, body, tree):
    from . import groom_tree

    out = groom_tree.output_node(tree)
    if out is None:
        return None, "グルームツリーに出力ノードがありません"
    remove_generated(body.name)
    mode = out.bind_mode
    if mode == "ARMATURE" and not binding.body_armatures(body):
        report({"WARNING"}, "アーマチュアが見つからないためサーフェス変形で追従します")
        mode = "SURFACE"

    with binding.rest_pose(context, body, P.rest_pose):
        bone_names = binding.bone_names_of(body) if mode == "ARMATURE" else None
        sampler = BodySampler(context, body, P.mask_group, bone_names)
        lv = Levels(P, sampler)
        scale = lv.scale
        ectx = EvalContext(report, context, P, body, sampler, lv, out)
        batches = groom_tree.evaluate(tree, ectx)
        n_guides = sum(len(b.splines) for b in batches)
        if n_guides == 0:
            return None, "ガイドがありません。ガイドを描くか自動生成し、ノードで出力に繋いでください"

        rng = random.Random(P.seed + 7919)
        paths = []
        for batch in batches:
            need_field = any(s.kind == guides.KIND_SKIRT for s in batch.splines)
            paths += paths_from_batch(batch, sampler, ectx.field if need_field else None, rng, scale)
        if not paths:
            return None, "つるを生成できませんでした"

        use_sway = out.use_sway and out.sway_strength > 0.0
        for p in paths:
            ts = p.tparams or [None] * len(p.points)
            p.sway = [sway_weight(pt.z, lv.water_z, lv.H, p.kind, t) if use_sway else 0.0
                      for pt, t in zip(p.points, ts)]

        b = MeshBuilder()
        for p in paths:
            add_tube(b, p, out.ring_res, rng)
        stem_faces = len(b.faces)
        for p in paths:
            add_leaves(b, p, rng, scale)
        leaves = (len(b.faces) - stem_faces) // 8

        name = body.name + "_VineDress"
        me = build_mesh(b, name, sampler.to_local)
        obj = bpy.data.objects.new(name, me)
        obj["vine_dress_source"] = body.name
        colls = body.users_collection or (context.scene.collection,)
        colls[0].objects.link(obj)
        binding.attach(obj, body)
        binding.ensure_materials(obj)
        assign_vertex_groups(obj, b, bone_names if mode == "ARMATURE" else set(),
                             binding.SWAY_GROUP if use_sway else "")

        stats = {"paths": len(paths), "guides": n_guides, "leaves": leaves, "verts": len(b.verts)}
        if mode == "ARMATURE":
            binding.add_armature(obj, body)
        elif mode == "SURFACE":
            context.view_layer.update()
            mod = binding.add_surface_deform(context, obj, body)
            if not mod.is_bound:
                stats["bind_failed"] = True
        if use_sway:
            binding.add_sway(obj, out, scale)
    return obj, stats


def format_stats(stats, seconds):
    return "つる生成完了: ガイド %d 本 → つる %d 本 / 葉 %d 枚 / 頂点 %d (%.1f秒)" % (
        stats["guides"], stats["paths"], stats["leaves"], stats["verts"], seconds)
