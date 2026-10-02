"""Making the generated vines follow the animated target object."""

from contextlib import contextmanager

import bpy



def target_armatures(target):
    return [m.object for m in target.modifiers if m.type == "ARMATURE" and m.object]


def bone_names_of(target):
    arms = target_armatures(target)
    if not arms:
        return None
    names = set()
    for a in arms:
        names.update(b.name for b in a.data.bones)
    return names


@contextmanager
def rest_pose(context, target, enabled=True):
    saved = []
    if enabled:
        for arm in target_armatures(target):
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


def attach(obj, target):
    """Parent to the target with an identity offset so local coords == target local coords."""
    obj.parent = target
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis.identity()
    obj.matrix_world = target.matrix_world.copy()


def add_armature(obj, target):
    src = next((m for m in target.modifiers if m.type == "ARMATURE" and m.object), None)
    if src is None:
        return None
    mod = obj.modifiers.new("VineWrap_Armature", "ARMATURE")
    mod.object = src.object
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    mod.use_deform_preserve_volume = src.use_deform_preserve_volume
    return mod


def add_surface_deform(context, obj, target):
    mod = obj.modifiers.new("VineWrap_SurfaceDeform", "SURFACE_DEFORM")
    mod.target = target
    mod.falloff = 4.0
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.surfacedeform_bind(modifier=mod.name)
    # Binding happens on the next evaluation.
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
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
    obj.data.materials.append(_material("VineWrap_Stem", (0.12, 0.18, 0.06, 1.0), 0.6))
    obj.data.materials.append(_material("VineWrap_Leaf", (0.1, 0.35, 0.08, 1.0), 0.45, 0.1))
