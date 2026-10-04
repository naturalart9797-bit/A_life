# SPDX-License-Identifier: GPL-3.0-or-later
"""geometry の結果を Blender のメッシュ・マテリアル・シェイプキーにする。"""

import math

import bpy

from . import geometry

KINDS = ('PETAL', 'CORONA', 'STAMEN', 'PISTIL', 'SEPAL', 'STEM')
MAT_LABEL = {
    'PETAL': "Petal",
    'CORONA': "Corona",
    'STAMEN': "Stamen",
    'PISTIL': "Pistil",
    'SEPAL': "Sepal",
    'STEM': "Stem",
    'WATER': "Water",
}
MAT_VERSION = 4
COLOR_ATTR = "wf_color"


def colors_of(s):
    return {
        "petal_base": tuple(s.color_petal_base),
        "petal_tip": tuple(s.color_petal_tip),
        "corona_base": tuple(s.color_corona_base),
        "corona_rim": tuple(s.color_corona_rim),
        "stamen": tuple(s.color_stamen),
        "filament": tuple(s.color_filament),
        "pistil": tuple(s.color_pistil),
        "pistil_base": tuple(s.color_filament),
        "sepal_base": tuple(s.color_sepal_base),
        "sepal_tip": tuple(s.color_sepal_tip),
        "ovary": tuple(s.color_ovary),
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


# 部品ごとの質感: 筋の本数 (UV の x 方向), 細かい筋の倍率, 粗さ, つや (sheen), 透け, 凹凸
LOOKS = {
    'PETAL':  dict(veins=13.0, fine=3.0, rough=0.42, sheen=0.45, trans=1.0, bump=0.10, cells=520.0),
    'CORONA': dict(veins=90.0, fine=2.5, rough=0.38, sheen=0.35, trans=0.8, bump=0.16, cells=700.0),
    'SEPAL':  dict(veins=9.0, fine=4.0, rough=0.5, sheen=0.2, trans=0.6, bump=0.12, cells=380.0),
    'STAMEN': dict(veins=0.0, fine=0.0, rough=0.75, sheen=0.1, trans=0.2, bump=0.5, cells=260.0),
    'PISTIL': dict(veins=0.0, fine=0.0, rough=0.6, sheen=0.2, trans=0.3, bump=0.25, cells=200.0),
    'STEM':   dict(veins=28.0, fine=2.0, rough=0.5, sheen=0.3, trans=0.15, bump=0.06, cells=300.0),
}


def _math(nt, op, a, b=None, loc=(0, 0)):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = loc
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[i].default_value = v
        else:
            nt.links.new(v, n.inputs[i])
    return n.outputs[0]


def _vein_mask(nt, x, count, sharp, distort, loc):
    """UV の x に沿った細い筋 (1 = 筋の上)。"""
    v = _math(nt, 'MULTIPLY', x, count, loc)
    v = _math(nt, 'ADD', v, distort, (loc[0] + 160, loc[1]))
    v = _math(nt, 'MULTIPLY', v, 2 * math.pi, (loc[0] + 320, loc[1]))
    v = _math(nt, 'SINE', v, None, (loc[0] + 480, loc[1]))
    v = _math(nt, 'ABSOLUTE', v, None, (loc[0] + 640, loc[1]))
    v = _math(nt, 'SUBTRACT', 1.0, v, (loc[0] + 800, loc[1]))
    return _math(nt, 'POWER', v, sharp, (loc[0] + 960, loc[1]))


def build_material(mat, kind):
    """筋 (葉脈)・まだら・細胞の凹凸・光の透けを持つ、花びららしいマテリアル。"""
    if hasattr(mat, "use_nodes") and not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (2600, 0)
    if kind == 'WATER':
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
        bsdf.location = (2300, 0)
        _set_input(bsdf, ["Base Color"], (0.85, 0.95, 1.0, 1.0))
        _set_input(bsdf, ["Roughness"], 0.0)
        _set_input(bsdf, ["IOR"], 1.333)
        _set_input(bsdf, ["Transmission Weight", "Transmission"], 1.0)
        nt.links.new(bsdf.outputs[0], out.inputs["Surface"])
        mat.diffuse_color = (0.4, 0.7, 1.0, 0.4)
        return
    look = LOOKS[kind]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    tc.location = (-1400, 0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (-1200, 200)
    nt.links.new(tc.outputs["UV"], sep.inputs[0])
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = COLOR_ATTR
    attr.location = (-200, 500)

    # 筋をゆらすノイズ
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.location = (-1200, -100)
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 6.0
    nt.links.new(tc.outputs["UV"], noise.inputs["Vector"])
    distort = _math(nt, 'MULTIPLY', _math(nt, 'SUBTRACT', noise.outputs["Fac"], 0.5, (-1000, -100)), 1.6, (-850, -100))

    vein_s = nt.nodes.new("ShaderNodeValue")
    vein_s.name = "wf_vein_strength"
    vein_s.label = "筋の濃さ"
    vein_s.location = (-200, 700)
    vein_s.outputs[0].default_value = 0.35

    if look["veins"] > 0:
        # 太い筋 (やわらかく、長さ方向に濃淡がある) + 細い筋 (うすく)
        main = _vein_mask(nt, sep.outputs["X"], look["veins"], 2.0, distort, (-800, 300))
        fine = _vein_mask(nt, sep.outputs["X"], look["veins"] * look["fine"], 4.0, distort, (-800, 100))
        stretch = nt.nodes.new("ShaderNodeCombineXYZ")
        stretch.location = (-1000, 600)
        nt.links.new(_math(nt, 'MULTIPLY', sep.outputs["X"], look["veins"] * 0.7, (-1200, 650)), stretch.inputs[0])
        nt.links.new(_math(nt, 'MULTIPLY', sep.outputs["Y"], 2.5, (-1200, 550)), stretch.inputs[1])
        streak = nt.nodes.new("ShaderNodeTexNoise")
        streak.location = (-800, 600)
        streak.inputs["Scale"].default_value = 1.0
        streak.inputs["Detail"].default_value = 4.0
        nt.links.new(stretch.outputs[0], streak.inputs["Vector"])
        mod = _math(nt, 'MULTIPLY', _math(nt, 'SUBTRACT', streak.outputs["Fac"], 0.25, (-600, 600)), 1.6, (-450, 600))
        mod = _math(nt, 'MINIMUM', _math(nt, 'MAXIMUM', mod, 0.0, (-300, 600)), 1.0, (-150, 600))
        main = _math(nt, 'MULTIPLY', main, mod, (300, 300))
        mask = _math(nt, 'MAXIMUM', main, _math(nt, 'MULTIPLY', fine, 0.25, (300, 100)), (450, 200))
    else:
        mask = _math(nt, 'MULTIPLY', noise.outputs["Fac"], 0.0, (450, 200))

    # 色: 頂点カラー → 筋で少し濃く → まだら
    vfac = _math(nt, 'MULTIPLY', mask, vein_s.outputs[0], (650, 300))
    dark = nt.nodes.new("ShaderNodeHueSaturation")
    dark.location = (650, 600)
    dark.inputs["Saturation"].default_value = 1.2
    dark.inputs["Value"].default_value = 0.8
    nt.links.new(attr.outputs["Color"], dark.inputs["Color"])
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = 'RGBA'
    mix.location = (900, 500)
    nt.links.new(vfac, mix.inputs[0])
    nt.links.new(attr.outputs["Color"], mix.inputs[6])
    nt.links.new(dark.outputs[0], mix.inputs[7])

    mott = nt.nodes.new("ShaderNodeTexNoise")
    mott.location = (650, 900)
    mott.inputs["Scale"].default_value = 3.0
    mott.inputs["Detail"].default_value = 3.0
    nt.links.new(tc.outputs["Object"], mott.inputs["Vector"])
    hsv = nt.nodes.new("ShaderNodeHueSaturation")
    hsv.location = (1150, 600)
    val = _math(nt, 'ADD', _math(nt, 'MULTIPLY', mott.outputs["Fac"], 0.22, (900, 900)), 0.89, (1050, 900))
    nt.links.new(val, hsv.inputs["Value"])
    nt.links.new(mix.outputs[2], hsv.inputs["Color"])
    color = hsv.outputs[0]

    # 表皮の細胞のこまかい凹凸 + 筋のふくらみ
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.location = (650, -300)
    vor.inputs["Scale"].default_value = look["cells"]
    nt.links.new(tc.outputs["UV"], vor.inputs["Vector"])
    height = _math(nt, 'ADD', mask, _math(nt, 'MULTIPLY', vor.outputs["Distance"], 0.6, (900, -300)), (1100, -200))
    bump = nt.nodes.new("ShaderNodeBump")
    bump.location = (1350, -200)
    bump.inputs["Strength"].default_value = look["bump"]
    if "Distance" in bump.inputs:
        bump.inputs["Distance"].default_value = 0.004
    nt.links.new(height, bump.inputs["Height"])

    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (1650, 300)
    nt.links.new(color, bsdf.inputs["Base Color"])
    nt.links.new(bump.outputs[0], bsdf.inputs["Normal"])
    _set_input(bsdf, ["Roughness"], look["rough"])
    _set_input(bsdf, ["Sheen Weight", "Sheen"], look["sheen"])
    _set_input(bsdf, ["Sheen Roughness"], 0.35)
    _set_input(bsdf, ["Specular IOR Level", "Specular"], 0.35)

    # 光の透け (裏から光が当たると明るく光る)
    bright = nt.nodes.new("ShaderNodeHueSaturation")
    bright.location = (1650, -150)
    bright.inputs["Saturation"].default_value = 1.15
    bright.inputs["Value"].default_value = 1.1
    nt.links.new(color, bright.inputs["Color"])
    trans = nt.nodes.new("ShaderNodeBsdfTranslucent")
    trans.location = (1900, -150)
    nt.links.new(bright.outputs[0], trans.inputs["Color"])
    nt.links.new(bump.outputs[0], trans.inputs["Normal"])
    tval = nt.nodes.new("ShaderNodeValue")
    tval.name = "wf_translucency"
    tval.label = "透け感"
    tval.location = (1650, -400)
    tval.outputs[0].default_value = 0.35
    tfac = _math(nt, 'MULTIPLY', tval.outputs[0], look["trans"], (1900, -400))
    ms = nt.nodes.new("ShaderNodeMixShader")
    ms.location = (2300, 0)
    nt.links.new(tfac, ms.inputs[0])
    nt.links.new(bsdf.outputs[0], ms.inputs[1])
    nt.links.new(trans.outputs[0], ms.inputs[2])
    nt.links.new(ms.outputs[0], out.inputs["Surface"])
    mat.diffuse_color = {'PETAL': (1.0, 0.8, 0.3, 1), 'CORONA': (1.0, 0.55, 0.1, 1),
                         'STAMEN': (0.95, 0.75, 0.1, 1), 'PISTIL': (0.85, 0.85, 0.35, 1),
                         'SEPAL': (0.25, 0.45, 0.1, 1), 'STEM': (0.2, 0.42, 0.12, 1)}[kind]


def material_for(owner_name, kind):
    name = "WF_%s_%s" % (owner_name, MAT_LABEL[kind])
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    if mat.get("wf_version") != MAT_VERSION:
        build_material(mat, kind)
        mat["wf_version"] = MAT_VERSION
    return mat


def update_material_values(mat, s):
    if not mat or not mat.node_tree:
        return
    n = mat.node_tree.nodes.get("wf_vein_strength")
    if n:
        n.outputs[0].default_value = s.vein_strength
    n = mat.node_tree.nodes.get("wf_translucency")
    if n:
        n.outputs[0].default_value = s.translucency


def ensure_slots(obj, kinds, owner):
    mesh = obj.data
    mats = [material_for(owner.name, k) for k in kinds]
    if list(mesh.materials) != mats:
        mesh.materials.clear()
        for m in mats:
            mesh.materials.append(m)
    for m in mats:
        update_material_values(m, owner.water_flower)


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


def write_mesh(obj, pieces, kinds, smooth, owner=None):
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
    ensure_slots(obj, kinds, owner or obj)


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
    write_mesh(obj, pieces, KINDS, s.smooth, obj)
    ensure_solidify(obj, s.thickness)
    s.water_volume = vol
    s.water_level = info["water_level"]
    if s.anim_baked:
        s.anim_baked = False
        s.anim_stale = True
    w = s.water_object
    if s.show_water and water is not None:
        w = ensure_water_object(obj)
        write_mesh(w, [water], ('WATER',), True, obj)
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
