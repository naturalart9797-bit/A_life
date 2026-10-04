"""Making the generated vines follow the animated target object."""

import math
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


def _new_material(name):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    try:
        mat.use_nodes = True  # deprecated/no-op in Blender 5
    except (AttributeError, TypeError):
        pass
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    nt = mat.node_tree
    if nt is not None:
        nt.nodes.clear()
    return mat, nt


class _G:
    """Tiny node-graph helper (version tolerant: links by socket name)."""

    def __init__(self, nt):
        self.nt = nt
        self.x = -1400

    def node(self, kind, **inputs):
        n = self.nt.nodes.new(kind)
        n.location = (self.x, 0)
        self.x += 40
        for k, v in inputs.items():
            self.set(n, k, v)
        return n

    def set(self, n, name, v):
        sock = n.inputs.get(name) if isinstance(name, str) else n.inputs[name]
        if sock is None:
            return
        if isinstance(v, bpy.types.NodeSocket):
            self.nt.links.new(v, sock)
        elif isinstance(v, bpy.types.Node):
            self.nt.links.new(v.outputs[0], sock)
        else:
            sock.default_value = v

    def math(self, op, a, b=0.0, c=0.0, clamp=False):
        n = self.node("ShaderNodeMath")
        n.operation = op
        n.use_clamp = clamp
        for i, v in enumerate((a, b, c)):
            self.set(n, i, v)
        return n.outputs[0]

    def map_range(self, v, fmin, fmax, tmin, tmax):
        n = self.node("ShaderNodeMapRange")
        n.clamp = True
        self.set(n, "Value", v)
        self.set(n, "From Min", fmin)
        self.set(n, "From Max", fmax)
        self.set(n, "To Min", tmin)
        self.set(n, "To Max", tmax)
        return n.outputs[0]

    def attr(self, name, out="Fac"):
        n = self.node("ShaderNodeAttribute")
        n.attribute_name = name
        return n.outputs[out]


def stem_material(style):
    mat, nt = _new_material("VineWrap_Stem_%s" % style.name)
    if nt is None:
        return mat
    g = _G(nt)
    ramp = g.node("ShaderNodeValToRGB")
    g.set(ramp, "Fac", g.attr("vine_age"))
    els = ramp.color_ramp.elements
    els[0].position, els[0].color = 0.0, (*style.stem_young_color, 1.0)
    els[1].position, els[1].color = 1.0, (*style.stem_color, 1.0)
    mid = els.new(0.35)
    mid.color = tuple((a * 0.5 + b * 0.5) for a, b in zip(els[0].color, els[1].color))
    value = g.math("MULTIPLY_ADD", g.attr("vine_rand"), 0.3, 0.85)
    hsv = g.node("ShaderNodeHueSaturation", Color=ramp.outputs["Color"], Value=value)

    tc = g.node("ShaderNodeTexCoord")
    mp = g.node("ShaderNodeMapping")
    g.set(mp, "Vector", tc.outputs["UV"])
    g.set(mp, "Scale", (6.0, 0.6, 1.0))
    nz = g.node("ShaderNodeTexNoise", Scale=20.0, Detail=6.0)
    g.set(nz, "Vector", mp.outputs[0])
    bump = g.node("ShaderNodeBump", Strength=style.bark_bump * 0.6)
    g.set(bump, "Height", nz.outputs["Fac"])

    bsdf = g.node("ShaderNodeBsdfPrincipled", Roughness=0.75)
    g.set(bsdf, "Base Color", hsv.outputs[0])
    g.set(bsdf, "Normal", bump.outputs["Normal"])
    out = g.node("ShaderNodeOutputMaterial")
    nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = (*style.stem_color, 1.0)
    return mat


def leaf_material(style):
    mat, nt = _new_material("VineWrap_Leaf_%s" % style.name)
    if nt is None:
        return mat
    g = _G(nt)
    tc = g.node("ShaderNodeTexCoord")
    sep = g.node("ShaderNodeSeparateXYZ")
    g.set(sep, "Vector", tc.outputs["UV"])
    u, v = sep.outputs["X"], sep.outputs["Y"]
    du = g.math("ABSOLUTE", g.math("SUBTRACT", u, 0.5))
    midrib = g.map_range(du, 0.0, 0.025, 1.0, 0.0)
    coord = g.math("MULTIPLY", g.math("SUBTRACT", v, g.math("MULTIPLY", du, 1.2)), math.pi * 9.0)
    side = g.map_range(g.math("ABSOLUTE", g.math("SINE", coord)), 0.0, 0.12, 0.6, 0.0)
    side = g.math("MULTIPLY", side, g.map_range(du, 0.05, 0.45, 1.0, 0.0))
    vein = g.math("MULTIPLY", g.math("MAXIMUM", midrib, side), style.vein_strength)
    mottle = g.node("ShaderNodeTexNoise", Scale=6.0, Detail=4.0)
    g.set(mottle, "Vector", tc.outputs["UV"])
    rand = g.attr("vine_rand")

    image = style.leaf_image
    if image is not None:
        tex = g.node("ShaderNodeTexImage")
        tex.image = image
        g.set(tex, "Vector", tc.outputs["UV"])
        hue = g.math("MULTIPLY_ADD", rand, 0.06, 0.47)
        val = g.math("MULTIPLY_ADD", rand, 0.3, 0.85)
        color = g.node("ShaderNodeHueSaturation", Color=tex.outputs["Color"], Hue=hue, Value=val).outputs[0]
    else:
        val = g.math("ADD", g.math("MULTIPLY_ADD", vein, 0.7, 1.0),
                     g.math("MULTIPLY", g.math("SUBTRACT", mottle.outputs["Fac"], 0.5), 0.35))
        sat = g.math("MULTIPLY_ADD", vein, -0.35, 1.0)
        color = g.node("ShaderNodeHueSaturation", Color=g.attr("vine_color", "Color"), Value=val,
                       Saturation=sat).outputs[0]

    bsdf = g.node("ShaderNodeBsdfPrincipled", Roughness=style.leaf_roughness)
    g.set(bsdf, "Base Color", color)
    if image is None:
        bump = g.node("ShaderNodeBump", Strength=0.2)
        g.set(bump, "Height", vein)
        g.set(bsdf, "Normal", bump.outputs["Normal"])
    trans = g.node("ShaderNodeBsdfTranslucent")
    g.set(trans, "Color", color)
    mix = g.node("ShaderNodeMixShader")
    g.set(mix, 0, style.leaf_translucency)
    nt.links.new(bsdf.outputs[0], mix.inputs[1])
    nt.links.new(trans.outputs[0], mix.inputs[2])
    surface = mix.outputs[0]
    if image is not None:
        clear = g.node("ShaderNodeBsdfTransparent")
        cut = g.node("ShaderNodeMixShader")
        g.set(cut, 0, tex.outputs["Alpha"])
        nt.links.new(clear.outputs[0], cut.inputs[1])
        nt.links.new(surface, cut.inputs[2])
        surface = cut.outputs[0]
        for attr, val_ in (("surface_render_method", "DITHERED"), ("blend_method", "HASHED")):
            try:
                setattr(mat, attr, val_)
                break
            except (AttributeError, TypeError):
                continue
    out = g.node("ShaderNodeOutputMaterial")
    nt.links.new(surface, out.inputs["Surface"])
    mat.diffuse_color = (*style.leaf_color, 1.0)
    return mat
