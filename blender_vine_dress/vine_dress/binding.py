"""Making the generated vines follow the animated body, plus underwater sway."""

from contextlib import contextmanager

import bpy

SWAY_GROUP = "VineSway"


def body_armatures(body):
    return [m.object for m in body.modifiers if m.type == "ARMATURE" and m.object]


def bone_names_of(body):
    arms = body_armatures(body)
    if not arms:
        return None
    names = set()
    for a in arms:
        names.update(b.name for b in a.data.bones)
    return names


@contextmanager
def rest_pose(context, body, enabled=True):
    saved = []
    if enabled:
        for arm in body_armatures(body):
            saved.append((arm.data, arm.data.pose_position))
            arm.data.pose_position = "REST"
        if saved:
            context.view_layer.update()
    try:
        yield
    finally:
        for data, pos in saved:
            data.pose_position = pos
        if saved:
            context.view_layer.update()


def attach(obj, body):
    """Parent to the body with an identity offset so local coords == body local coords."""
    obj.parent = body
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()


def add_armature(obj, body):
    src = next((m for m in body.modifiers if m.type == "ARMATURE" and m.object), None)
    if src is None:
        return None
    mod = obj.modifiers.new("VineDress_Armature", "ARMATURE")
    mod.object = src.object
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    mod.use_deform_preserve_volume = src.use_deform_preserve_volume
    return mod


def add_surface_deform(context, obj, body):
    mod = obj.modifiers.new("VineDress_SurfaceDeform", "SURFACE_DEFORM")
    mod.target = body
    mod.falloff = 4.0
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.surfacedeform_bind(modifier=mod.name)
    # Binding happens on the next evaluation.
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
    return mod


def add_sway(obj, P, scale):
    tex_name = obj.name + "_SwayNoise"
    tex = bpy.data.textures.get(tex_name) or bpy.data.textures.new(tex_name, "CLOUDS")
    tex.cloud_type = "COLOR"
    tex.noise_scale = P.sway_scale * scale
    tex.noise_depth = 1

    empty = bpy.data.objects.new(obj.name + "_SwayDriver", None)
    empty.empty_display_type = "SPHERE"
    empty.empty_display_size = 0.05 * scale
    empty["vine_dress_source"] = obj.get("vine_dress_source", "")
    empty.hide_render = True
    for coll in obj.users_collection:
        coll.objects.link(empty)
    empty.parent = obj
    speed = P.sway_speed * scale
    for axis, k in ((0, 1.0), (1, 0.7), (2, 0.4)):
        fc = empty.driver_add("location", axis)
        fc.driver.type = "SCRIPTED"
        fc.driver.expression = "frame*%.6f" % (speed * k)

    mod = obj.modifiers.new("VineDress_Sway", "DISPLACE")
    mod.texture = tex
    mod.texture_coords = "OBJECT"
    mod.texture_coords_object = empty
    mod.direction = "RGB_TO_XYZ"
    mod.space = "GLOBAL"
    mod.mid_level = 0.5
    mod.strength = P.sway_strength * scale * 2.0
    mod.vertex_group = SWAY_GROUP
    return mod


def _material(name, color, roughness, sss=0.0):
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return mat
    bsdf.inputs["Roughness"].default_value = roughness
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "vine_color"
    attr.location = (bsdf.location.x - 300, bsdf.location.y)
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    for key in ("Subsurface Weight", "Subsurface"):
        if key in bsdf.inputs and sss:
            bsdf.inputs[key].default_value = sss
            break
    mat.diffuse_color = color
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    return mat


def ensure_materials(obj):
    obj.data.materials.append(_material("VineDress_Stem", (0.12, 0.18, 0.06, 1.0), 0.6))
    obj.data.materials.append(_material("VineDress_Leaf", (0.1, 0.35, 0.08, 1.0), 0.45, 0.1))
