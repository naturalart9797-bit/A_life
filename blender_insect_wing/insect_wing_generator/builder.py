"""Blender geometry construction for generated wing venation."""

import math

import bpy
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt

from . import venation


# vein kind -> (radius multiplier, taper toward the margin)
VEIN_WIDTH = {
    "costa": (1.6, 0.55),
    "subcosta": (1.25, 0.55),
    "primary": (1.15, 0.45),
    "intercalary": (0.75, 0.6),
    "margin": (0.8, 1.0),
    "cross": (0.42, 1.0),
}


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------

def _set_input(node, names, value):
    for n in names:
        sock = node.inputs.get(n)
        if sock is not None:
            try:
                sock.default_value = value
            except (TypeError, ValueError):
                pass
            return True
    return False


def _principled(mat):
    mat.use_nodes = True
    for node in mat.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")


def membrane_material(color, alpha, iridescence):
    name = "IW_Membrane"
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    bsdf = _principled(mat)
    _set_input(bsdf, ["Base Color"], (color[0], color[1], color[2], 1.0))
    _set_input(bsdf, ["Alpha"], alpha)
    _set_input(bsdf, ["Roughness"], 0.12)
    _set_input(bsdf, ["Transmission Weight", "Transmission"], 0.6)
    _set_input(bsdf, ["IOR"], 1.56)
    # Blender 4.2+: thin-film interference gives the iridescent sheen
    _set_input(bsdf, ["Thin Film Thickness"], 480.0 * iridescence)
    _set_input(bsdf, ["Thin Film IOR"], 1.56)
    mat.diffuse_color = (color[0], color[1], color[2], alpha)
    if hasattr(mat, "blend_method"):
        try:
            mat.blend_method = "BLEND"
        except TypeError:
            pass
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    if hasattr(mat, "use_backface_culling"):
        mat.use_backface_culling = False
    if hasattr(mat, "show_transparent_back"):
        mat.show_transparent_back = True
    return mat


def solid_material(name, color, roughness=0.45):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    bsdf = _principled(mat)
    _set_input(bsdf, ["Base Color"], (color[0], color[1], color[2], 1.0))
    _set_input(bsdf, ["Roughness"], roughness)
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    return mat


# ---------------------------------------------------------------------------
# Height field (camber + corrugation) shared by membrane and veins
# ---------------------------------------------------------------------------

class Surface:
    def __init__(self, shape, camber, twist):
        self.shape = shape
        self.camber = camber
        self.twist = twist
        self.L = shape.p.length

    def z(self, x, y):
        u = min(max(x / self.L, 0.0), 1.0)
        w = max(self.shape.width(u), 1e-6)
        v = min(max((self.shape.y_le(u) - y) / w, 0.0), 1.0)
        # arched chordwise profile, fading toward the tip
        z = self.camber * w * math.sin(math.pi * v) * (1.0 - 0.6 * u)
        # spanwise twist: trailing edge drops toward the tip
        z -= self.twist * u * u * (self.shape.y_le(u) - y)
        return z


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------

def _link(obj, collection, parent):
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent


def build_membrane(name, res, surf, mirror, mat, collection, parent):
    outline = res.outline
    L = res.shape.p.length
    # interior sample points so the camber is visible on the membrane
    xs = [q[0] for q in outline]
    ys = [q[1] for q in outline]
    step = (max(ys) - min(ys)) / 10.0
    pts = list(outline)
    nb = len(outline)
    x = min(xs) + step * 0.5
    while x < max(xs):
        y = min(ys) + step * 0.5
        while y < max(ys):
            if venation.point_in_polygon((x, y), outline):
                pts.append((x, y))
            y += step
        x += step
    edges = [(i, (i + 1) % nb) for i in range(nb)]
    faces = [list(range(nb))]
    out = delaunay_2d_cdt([Vector(q) for q in pts], edges, faces, 1, L * 1e-6)
    verts2d, _e, tris = out[0], out[1], out[2]
    sx = -1.0 if mirror else 1.0
    verts = [(sx * v.x, v.y, surf.z(v.x, v.y)) for v in verts2d]
    if mirror:
        tris = [list(reversed(t)) for t in tris]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], tris)
    mesh.update()
    for poly in mesh.polygons:
        poly.use_smooth = True
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    _link(obj, collection, parent)
    return obj


def build_veins(name, res, surf, mirror, thickness, mat, collection, parent,
                resolution=2):
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = thickness
    curve.bevel_resolution = resolution
    curve.use_fill_caps = True
    sx = -1.0 if mirror else 1.0
    step = res.shape.p.length / 250.0
    for v in res.veins:
        mult, taper = VEIN_WIDTH.get(v.kind, (0.5, 1.0))
        pts = v.pts
        if v.kind != "cross":
            pts = venation._resample_polyline(pts, step)
        if len(pts) < 2:
            continue
        sp = curve.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        n = len(pts)
        for i, q in enumerate(pts):
            t = i / (n - 1)
            if v.kind == "margin" or v.kind == "cross":
                r = mult
            else:
                r = mult * (1.0 - (1.0 - taper) * t)
            sp.points[i].co = (sx * q[0], q[1], surf.z(q[0], q[1]), 1.0)
            sp.points[i].radius = r
    curve.materials.append(mat)
    obj = bpy.data.objects.new(name, curve)
    _link(obj, collection, parent)
    return obj


def build_pterostigma(name, res, surf, mirror, thickness, mat, collection, parent):
    poly = res.pterostigma
    if not poly:
        return None
    sx = -1.0 if mirror else 1.0
    n = len(poly) // 2
    top = poly[:n]
    bot = list(reversed(poly[n:]))
    verts = []
    for q in top + bot:
        verts.append((sx * q[0], q[1], surf.z(q[0], q[1]) + thickness * 0.6))
    faces = []
    for i in range(n - 1):
        f = [i, i + 1, n + i + 1, n + i]
        faces.append(list(reversed(f)) if mirror else f)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    _link(obj, collection, parent)
    sol = obj.modifiers.new("Thickness", "SOLIDIFY")
    sol.thickness = thickness * 1.2
    sol.offset = 0.0
    return obj


def build_wing(label, params, settings, collection, parent, offset, mirror, mats):
    res = venation.generate(params)
    surf = Surface(res.shape, settings.camber, settings.twist)
    root = bpy.data.objects.new(label, None)
    root.empty_display_size = params.length * 0.1
    _link(root, collection, parent)
    root.location = offset
    th = settings.vein_thickness * params.length
    build_membrane(label + "_Membrane", res, surf, mirror, mats["membrane"],
                   collection, root)
    build_veins(label + "_Veins", res, surf, mirror, th, mats["vein"],
                collection, root, settings.vein_bevel_resolution)
    if params.pterostigma:
        build_pterostigma(label + "_Pterostigma", res, surf, mirror, th,
                          mats["stigma"], collection, root)
    return root, res
