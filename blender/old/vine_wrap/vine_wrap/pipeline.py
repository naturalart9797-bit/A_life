"""Building the vine mesh from guides, auto generation, and auto update."""

import random
import time
import traceback

import bpy
from mathutils import Vector

from . import binding, guides
from .build import Attrs, paths_from_guide
from .growth import finalize, grow_vines
from .meshgen import MeshBuilder, add_leaves, add_rootlets, add_tube, assign_vertex_groups, build_mesh
from .sampler import BodySampler

SOURCE_PROP = "vine_wrap_source"


def get_target(context):
    P = context.scene.vine_wrap
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


def vine_objects(target):
    return [o for o in bpy.data.objects if o.get(SOURCE_PROP) == target.name]


def remove_generated(target):
    for obj in vine_objects(target):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)


def remove_guide(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.curves.remove(data)


def wrap_axis(P, target):
    if P.gen_axis == "WORLD_Z":
        return Vector((0.0, 0.0, 1.0))
    v = {"LOCAL_X": (1, 0, 0), "LOCAL_Y": (0, 1, 0), "LOCAL_Z": (0, 0, 1)}[P.gen_axis]
    return (target.matrix_world.to_3x3() @ Vector(v)).normalized()


# ======================================================================
# Build
# ======================================================================
def build_vines(report, context, target):
    P = context.scene.vine_wrap
    guides.ensure_style(P)
    objs = [o for o in guides.guide_objects(target) if o.vine_guide.enabled]
    remove_generated(target)
    if not objs:
        return None, "ガイドがありません。ガイドを描くか、ペイントして自動生成してください"
    mode = P.bind_mode
    if mode == "ARMATURE" and not binding.target_armatures(target):
        mode = "SURFACE" if target.data.shape_keys or any(
            m.type in {"MESH_SEQUENCE_CACHE", "CLOTH", "SOFT_BODY"} for m in target.modifiers) else "NONE"

    with binding.rest_pose(context, target, P.rest_pose):
        bone_names = binding.bone_names_of(target) if mode == "ARMATURE" else None
        sampler = BodySampler(context, target, "", bone_names)
        scale = target_scale(target, P)
        step = P.step_length * scale
        rng = random.Random(P.seed)
        paths = []
        n_splines = 0
        slots = {}  # style uid -> (stem slot, leaf slot)
        materials = []
        for obj in objs:
            style = guides.style_of(P, obj)
            A = Attrs.from_guide(style, obj.vine_guide, P)
            if style is not None and style.uid not in slots:
                slots[style.uid] = (len(materials), len(materials) + 1)
                materials += [binding.stem_material(style), binding.leaf_material(style)]
            if style is not None:
                A.mat_stem, A.mat_leaf = slots[style.uid]
            for pts, rad, cyc in guides.evaluated(obj):
                rp, rr = guides.resample(pts, rad, step, cyc)
                if len(rp) >= 2:
                    n_splines += 1
                    paths += paths_from_guide(rp, rr, cyc, A, sampler, rng, scale)
        if not paths:
            return None, "つるを生成できませんでした"
        b = MeshBuilder()
        for p in paths:
            add_tube(b, p, P.ring_res, rng, scale)
            add_rootlets(b, p, rng, scale)
        for p in paths:
            add_leaves(b, p, rng, scale)

        name = target.name + "_Vines"
        me = build_mesh(b, name, sampler.to_local)
        obj = bpy.data.objects.new(name, me)
        obj[SOURCE_PROP] = target.name
        colls = target.users_collection or (context.scene.collection,)
        colls[0].objects.link(obj)
        binding.attach(obj, target)
        for m in materials:
            obj.data.materials.append(m)
        assign_vertex_groups(obj, b, bone_names if mode == "ARMATURE" else set())
        obj.hide_select = True  # clicks go to the guides / target, not the generated mesh

        stats = {"guides": n_splines, "paths": len(paths), "leaves": b.leaf_count, "verts": len(b.verts),
                 "mode": mode}
        if mode == "ARMATURE":
            binding.add_armature(obj, target)
        elif mode == "SURFACE":
            context.view_layer.update()
            mod = binding.add_surface_deform(context, obj, target)
            stats["bind_failed"] = not mod.is_bound
    return obj, stats


def format_stats(stats, seconds):
    return "つる生成: ガイド %d 本 → つる %d 本 / 葉 %d 枚 / 頂点 %d (%.1f秒)" % (
        stats["guides"], stats["paths"], stats["leaves"], stats["verts"], seconds)


# ======================================================================
# Auto generation
# ======================================================================
def generate(report, context, target):
    P = context.scene.vine_wrap
    guides.ensure_style(P)
    if P.gen_replace:
        for o in guides.guide_objects(target):
            if o.vine_guide.auto:
                remove_guide(o)
    has_group = bool(P.gen_group) and P.gen_group in target.vertex_groups

    with binding.rest_pose(context, target, P.rest_pose):
        sampler = BodySampler(context, target, P.gen_group if has_group else "")
        scale = target_scale(target, P)
        rng = random.Random(P.seed)
        avoid = []
        if P.gen_avoid:
            for o in guides.guide_objects(target):
                for pts, _r, _c in guides.evaluated(o, 4):
                    avoid += pts
        paths = grow_vines(sampler, P, rng, scale, sampler.mask_at, wrap_axis(P, target), avoid)
        finalize(paths, sampler, P)
        hover = P.guide_hover * scale
        spacing = P.gen_ctrl_spacing * scale
        made = 0
        for path in paths:
            pts = [p + n * hover for p, n in zip(path.points, path.normals)]
            cp, cr = guides.decimate(pts, path.radii, spacing)
            if len(cp) < 2:
                continue
            guides.new_guide(context, target, cp, cr, snap=True, name=guides.unique_name("AutoVine"),
                             auto=True)
            made += 1
    return made


# ======================================================================
# Auto update (debounced)
# ======================================================================
_state = {"pending": set(), "last": 0.0, "busy": False, "hold": 0}
DELAY = 0.35


def schedule(target):
    if target is None or _state["busy"]:
        return
    scene = bpy.context.scene
    P = getattr(scene, "vine_wrap", None)
    if P is None or not P.auto_update:
        return
    _state["pending"].add(target.name)
    _state["last"] = time.time()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=DELAY)


def hold(on):
    """Tools call this so no rebuild happens in the middle of a drag."""
    _state["hold"] = max(0, _state["hold"] + (1 if on else -1))


def _tick():
    wait = DELAY - (time.time() - _state["last"])
    if _state["hold"] or wait > 0.0:
        return max(wait, 0.1)
    names = list(_state["pending"])
    _state["pending"].clear()
    for name in names:
        target = bpy.data.objects.get(name)
        if target is not None:
            run_build(bpy.context, target, lambda kind, msg: print("[Vine Wrap]", msg))
    return None


def run_build(context, target, report):
    if context.mode not in {"OBJECT", "EDIT_CURVE"}:
        return None
    _state["busy"] = True
    t0 = time.time()
    try:
        obj, info = build_vines(report, context, target)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        report({"ERROR"}, "生成に失敗しました: %s" % exc)
        return None
    finally:
        _state["busy"] = False
    if obj is None:
        report({"WARNING"}, info)
        return None
    msg = format_stats(info, time.time() - t0)
    if info.get("bind_failed"):
        report({"WARNING"}, msg + " ※サーフェス変形のバインドに失敗")
    else:
        report({"INFO"}, msg)
    return obj


@bpy.app.handlers.persistent
def on_depsgraph(scene, depsgraph):
    if _state["busy"]:
        return
    P = getattr(scene, "vine_wrap", None)
    if P is None or P.target is None or not P.auto_update:
        return
    target = P.target
    for u in depsgraph.updates:
        idb = getattr(u.id, "original", u.id)
        if isinstance(idb, bpy.types.Object) and idb.type == "CURVE" and idb.vine_guide.target == target:
            if u.is_updated_geometry or u.is_updated_transform:
                schedule(target)
                return
        elif isinstance(idb, bpy.types.Curve):
            for o in guides.guide_objects(target):
                if o.data == idb:
                    schedule(target)
                    return


def register():
    if on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(on_depsgraph)


def unregister():
    if on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(on_depsgraph)
