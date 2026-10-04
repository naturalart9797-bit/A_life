# SPDX-License-Identifier: GPL-3.0-or-later
"""geometry の結果を Blender のメッシュ・マテリアル・シェイプキーにする。"""

import bpy

from . import geometry

KINDS = ('PETAL', 'CORONA', 'STAMEN', 'STEM')
MAT_NAMES = {
    'PETAL': "WaterFlower_Petal",
    'CORONA': "WaterFlower_Corona",
    'STAMEN': "WaterFlower_Stamen",
    'STEM': "WaterFlower_Stem",
    'WATER': "WaterFlower_Water",
}
COLOR_ATTR = "wf_color"


def colors_of(s):
    return {
        "petal_base": tuple(s.color_petal_base),
        "petal_tip": tuple(s.color_petal_tip),
        "corona_base": tuple(s.color_corona_base),
        "corona_rim": tuple(s.color_corona_rim),
        "stamen": tuple(s.color_stamen),
        "stem": tuple(s.color_stem),
    }


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

def _set_input(node, names, value):
    for n in names:
        if n in node.inputs:
            try:
                node.inputs[n].default_value = value
            except (TypeError, ValueError):
                pass
            return


def ensure_material(kind):
    name = MAT_NAMES[kind]
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is None:
        return mat
    if kind == 'WATER':
        mat.diffuse_color = (0.4, 0.7, 1.0, 0.5)
        _set_input(bsdf, ["Base Color"], (0.8, 0.92, 1.0, 1.0))
        _set_input(bsdf, ["Roughness"], 0.02)
        _set_input(bsdf, ["IOR"], 1.333)
        _set_input(bsdf, ["Transmission Weight", "Transmission"], 1.0)
        return mat
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = COLOR_ATTR
    attr.location = (bsdf.location.x - 300, bsdf.location.y)
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    rough = {'PETAL': 0.5, 'CORONA': 0.45, 'STAMEN': 0.7, 'STEM': 0.55}[kind]
    _set_input(bsdf, ["Roughness"], rough)
    if kind in ('PETAL', 'CORONA'):
        _set_input(bsdf, ["Subsurface Weight", "Subsurface"], 0.12)
        _set_input(bsdf, ["Sheen Weight", "Sheen"], 0.15)
    mat.diffuse_color = {'PETAL': (1.0, 0.85, 0.4, 1), 'CORONA': (1.0, 0.6, 0.1, 1),
                         'STAMEN': (0.95, 0.85, 0.3, 1), 'STEM': (0.25, 0.45, 0.15, 1)}[kind]
    return mat


def ensure_slots(obj, kinds):
    mesh = obj.data
    if [m.name if m else "" for m in mesh.materials] == [MAT_NAMES[k] for k in kinds]:
        return
    mesh.materials.clear()
    for k in kinds:
        mesh.materials.append(ensure_material(k))


# ---------------------------------------------------------------------------
# mesh
# ---------------------------------------------------------------------------

def gather(pieces, kinds):
    verts, faces, mats, uvs, cols, ranges = [], [], [], [], [], []
    for pc in pieces:
        off = len(verts)
        mi = kinds.index(pc.kind) if pc.kind in kinds else 0
        verts.extend(pc.verts)
        uvs.extend(pc.uvs)
        cols.extend(pc.colors if pc.colors else [(1, 1, 1)] * len(pc.verts))
        f0 = len(faces)
        for f in pc.faces:
            faces.append(tuple(i + off for i in f))
            mats.append(mi)
        ranges.append((pc.name, off, len(verts), f0, len(faces)))
    return verts, faces, mats, uvs, cols, ranges


def write_mesh(obj, pieces, kinds, smooth):
    mesh = obj.data
    if mesh.shape_keys:
        obj.shape_key_clear()
    verts, faces, mats, uvs, cols, _ = gather(pieces, kinds)
    mesh.clear_geometry()
    mesh.from_pydata(verts, [], faces)
    mesh.polygons.foreach_set("material_index", mats)
    mesh.polygons.foreach_set("use_smooth", [smooth] * len(faces))

    uv = mesh.uv_layers.get("UVMap") or mesh.uv_layers.new(name="UVMap")
    loop_v = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loop_v)
    flat = []
    for vi in loop_v:
        flat.extend(uvs[vi])
    uv.data.foreach_set("uv", flat)

    if COLOR_ATTR not in mesh.attributes:
        mesh.attributes.new(COLOR_ATTR, 'FLOAT_COLOR', 'POINT')
    attr = mesh.attributes[COLOR_ATTR]
    flat = []
    for c in cols:
        flat.extend((c[0], c[1], c[2], 1.0))
    attr.data.foreach_set("color", flat)
    mesh.update()
    ensure_slots(obj, kinds)


def ensure_solidify(obj, thickness):
    mod = obj.modifiers.get("WF_Thickness")
    if thickness <= 0:
        if mod:
            obj.modifiers.remove(mod)
        return
    if mod is None:
        mod = obj.modifiers.new("WF_Thickness", 'SOLIDIFY')
        mod.offset = 0.0
        mod.use_even_offset = False
        mod.use_quality_normals = True
    mod.thickness = thickness


def ensure_water_object(obj):
    s = obj.water_flower
    w = s.water_object
    if w is None or w.name not in bpy.data.objects:
        mesh = bpy.data.meshes.new(obj.name + "_Water")
        w = bpy.data.objects.new(obj.name + "_Water", mesh)
        for col in obj.users_collection:
            col.objects.link(w)
        w.parent = obj
        w["water_flower_water"] = True
        s.water_object = w
    return w


def regenerate(obj, bloom=None):
    """設定どおりに花 (と水) を作り直す。シェイプキーのアニメーションは消える。"""
    s = obj.water_flower
    b = s.bloom if bloom is None else bloom
    pieces, water, vol, info = geometry.build_flower(s, b, colors_of(s), with_water=True)
    write_mesh(obj, pieces, KINDS, s.smooth)
    ensure_solidify(obj, s.thickness)
    s.water_volume = vol
    s.water_level = info["water_level"]
    if s.anim_baked:
        s.anim_baked = False
        s.anim_stale = True
    w = s.water_object
    if s.show_water and water is not None:
        w = ensure_water_object(obj)
        write_mesh(w, [water], ('WATER',), True)
        w.hide_viewport = False
        w.hide_render = False
    elif w is not None and w.name in bpy.data.objects:
        if w.data.shape_keys:
            w.shape_key_clear()
        w.hide_viewport = True
        w.hide_render = True


# ---------------------------------------------------------------------------
# animation (shape keys)
# ---------------------------------------------------------------------------

def bloom_curve(s):
    """[(frame, bloom)] を返す。"""
    def ease(t):
        return t * t * (3 - 2 * t)
    d = max(s.anim_duration, 2)
    f0 = s.anim_start
    seq = []
    if s.anim_mode == 'CLOSE':
        segs = [(1.0, 0.0, d)]
    elif s.anim_mode == 'OPEN':
        segs = [(0.0, 1.0, d)]
    elif s.anim_mode == 'CLOSE_OPEN':
        segs = [(1.0, 0.0, d), (0.0, 0.0, s.anim_hold), (0.0, 1.0, d)]
    else:
        segs = [(0.0, 1.0, d), (1.0, 1.0, s.anim_hold), (1.0, 0.0, d)]
    f = f0
    seq.append((f, segs[0][0]))
    for a, b, n in segs:
        for i in range(1, n + 1):
            seq.append((f + i, a + (b - a) * ease(i / n)))
        f += n
    return seq


def _flat_verts(pieces):
    flat = []
    for pc in pieces:
        for v in pc.verts:
            flat.extend(v)
    return flat


def bake_animation(obj, context=None):
    s = obj.water_flower
    n = s.anim_samples
    blooms = [i / (n - 1) for i in range(n)]
    colors = colors_of(s)
    # 基本形 = 満開
    regenerate(obj, bloom=1.0)
    w = s.water_object if (s.show_water and s.water_object) else None

    targets = [(obj, 0)]
    if w is not None:
        targets.append((w, 1))
    keys = {o.name: [] for o, _ in targets}
    for o, _ in targets:
        o.shape_key_add(name="Basis", from_mix=False)
    for b in blooms:
        pieces, water, _, _ = geometry.build_flower(s, b, colors, with_water=w is not None)
        for o, which in targets:
            data = _flat_verts(pieces) if which == 0 else _flat_verts([water])
            kb = o.shape_key_add(name="bloom_%.2f" % b, from_mix=False)
            if len(data) == len(kb.data) * 3:
                kb.data.foreach_set("co", data)
            keys[o.name].append(kb)

    seq = bloom_curve(s)
    prefs = bpy.context.preferences.edit
    old_interp = prefs.keyframe_new_interpolation_type
    prefs.keyframe_new_interpolation_type = 'LINEAR'
    try:
        step = 1.0 / (n - 1)
        for o, _ in targets:
            kd = o.data.shape_keys
            kd.use_relative = True
            if kd.animation_data:
                kd.animation_data_clear()
            for i, kb in enumerate(keys[o.name]):
                prev = None
                for idx, (frame, b) in enumerate(seq):
                    val = max(0.0, 1.0 - abs(b - blooms[i]) / step)
                    nxt = None
                    if idx + 1 < len(seq):
                        nb = seq[idx + 1][1]
                        nxt = max(0.0, 1.0 - abs(nb - blooms[i]) / step)
                    # 0 が続く所はキーを省く
                    if val == 0.0 and prev == 0.0 and (nxt in (0.0, None)):
                        prev = val
                        continue
                    kb.value = val
                    kb.keyframe_insert("value", frame=frame)
                    prev = val
    finally:
        prefs.keyframe_new_interpolation_type = old_interp

    scene = (context or bpy.context).scene
    scene.frame_end = max(scene.frame_end, seq[-1][0])
    s.anim_baked = True
    s.anim_stale = False
    return seq[-1][0] - seq[0][0]


def clear_animation(obj):
    s = obj.water_flower
    for o in (obj, s.water_object):
        if o is None:
            continue
        if o.data.shape_keys:
            if o.data.shape_keys.animation_data:
                o.data.shape_keys.animation_data_clear()
            o.shape_key_clear()
    s.anim_baked = False
    s.anim_stale = False
    regenerate(obj)
