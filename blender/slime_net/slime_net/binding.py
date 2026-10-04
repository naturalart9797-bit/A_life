"""Making the generated network follow the animated target object."""

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
    mod = obj.modifiers.new("SlimeNet_Armature", "ARMATURE")
    mod.object = src.object
    mod.use_vertex_groups = True
    mod.use_bone_envelopes = False
    mod.use_deform_preserve_volume = src.use_deform_preserve_volume
    return mod


def add_surface_deform(context, obj, target):
    mod = obj.modifiers.new("SlimeNet_SurfaceDeform", "SURFACE_DEFORM")
    mod.target = target
    mod.falloff = 4.0
    with context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.surfacedeform_bind(modifier=mod.name)
    # Binding happens on the next evaluation.
    context.view_layer.update()
    context.evaluated_depsgraph_get().update()
    return mod
