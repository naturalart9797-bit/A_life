"""
Arthur Rackham style ink & wash shader for Blender (4.2+ / 5.x, EEVEE)

アーサー・ラッカムの挿絵風ルックを作るスクリプト。
  - セピア系に褪色した水彩ウォッシュ（3段階トーン + ムラ + 顔料の溜まり）
  - 暗部にだけ入るペンのハッチング / クロスハッチング（揺らぎ付き）
  - ヴェラム紙の色とテクスチャ（スクリーン空間）
  - Freestyle による手描き風の揺れる輪郭線（強弱・はみ出し付き）

使い方:
  1. Blender の Scripting タブでこのファイルを開いて「Run Script」
     - メッシュを選択していれば、その色(Principled の Base Color)を引き継いで置き換え
     - 何も選択していなければデモシーンを作成
  2. コマンドライン:
     blender -b -P rackham_shader.py -- --demo --render /tmp/rackham.png

調整は各マテリアル内の「Rackham Ink & Wash」グループノードの入力で行う。
光源は Sun ライト1灯 + 弱い環境光が最も挿絵らしくなる。
"""

import math
import sys

import bpy

GROUP_NAME = "Rackham Ink & Wash"
LINESTYLE_NAME = "Rackham Pen"

PAPER = (0.92, 0.86, 0.72, 1.0)   # 黄ばんだヴェラム
INK = (0.13, 0.08, 0.05, 1.0)     # 焦げ茶のインク
UMBER = (0.42, 0.34, 0.24, 1.0)   # 影色（泥っぽいアンバー）


# --------------------------------------------------------------------------
# node helpers
# --------------------------------------------------------------------------

def _node(nodes, type_, x, y, **props):
    n = nodes.new(type_)
    n.location = (x, y)
    for k, v in props.items():
        setattr(n, k, v)
    return n


def _math(nt, op, a, b, x, y, clamp=False):
    """a, b は socket か数値。"""
    n = _node(nt.nodes, "ShaderNodeMath", x, y, operation=op, use_clamp=clamp)
    for i, v in enumerate((a, b)):
        if v is None:
            continue
        if isinstance(v, bpy.types.NodeSocket):
            nt.links.new(v, n.inputs[i])
        else:
            n.inputs[i].default_value = v
    return n.outputs[0]


def _smooth(nt, value, lo, hi, x, y, invert=False):
    """smoothstep(lo, hi, value)。lo/hi は socket 可。"""
    n = _node(nt.nodes, "ShaderNodeMapRange", x, y,
              interpolation_type="SMOOTHSTEP", clamp=True)
    nt.links.new(value, n.inputs["Value"])
    for name, v in (("From Min", lo), ("From Max", hi)):
        if isinstance(v, bpy.types.NodeSocket):
            nt.links.new(v, n.inputs[name])
        else:
            n.inputs[name].default_value = v
    n.inputs["To Min"].default_value = 1.0 if invert else 0.0
    n.inputs["To Max"].default_value = 0.0 if invert else 1.0
    return n.outputs["Result"]


def _mix(nt, blend, fac, a, b, x, y):
    n = _node(nt.nodes, "ShaderNodeMix", x, y, data_type="RGBA", blend_type=blend)
    for ident, v in (("Factor_Float", fac), ("A_Color", a), ("B_Color", b)):
        sock = next(s for s in n.inputs if s.identifier == ident)
        if isinstance(v, bpy.types.NodeSocket):
            nt.links.new(v, sock)
        else:
            sock.default_value = v
    return next(s for s in n.outputs if s.identifier == "Result_Color")


# --------------------------------------------------------------------------
# shader node group
# --------------------------------------------------------------------------

GROUP_INPUTS = [
    # name, socket type, default, min, max
    ("Base Color", "NodeSocketColor", (0.45, 0.40, 0.25, 1.0), None, None),
    ("Paper Color", "NodeSocketColor", PAPER, None, None),
    ("Ink Color", "NodeSocketColor", INK, None, None),
    ("Shadow Tint", "NodeSocketColor", UMBER, None, None),
    ("Desaturate", "NodeSocketFloat", 0.4, 0.0, 1.0),
    ("Highlight Wash", "NodeSocketFloat", 0.3, 0.0, 1.0),
    ("Light Threshold", "NodeSocketFloat", 0.62, 0.0, 1.0),
    ("Shadow Threshold", "NodeSocketFloat", 0.30, 0.0, 1.0),
    ("Band Softness", "NodeSocketFloat", 0.05, 0.001, 0.5),
    ("Wash Variation", "NodeSocketFloat", 0.22, 0.0, 1.0),
    ("Wash Scale", "NodeSocketFloat", 3.0, 0.0, 100.0),
    ("Granulation", "NodeSocketFloat", 0.12, 0.0, 1.0),
    ("Edge Darkening", "NodeSocketFloat", 0.35, 0.0, 1.0),
    ("Hatch Threshold", "NodeSocketFloat", 0.36, 0.0, 1.0),
    ("Cross Hatch Threshold", "NodeSocketFloat", 0.16, 0.0, 1.0),
    ("Hatch Density", "NodeSocketFloat", 30.0, 1.0, 500.0),
    ("Hatch Width", "NodeSocketFloat", 0.28, 0.0, 1.0),
    ("Hatch Wobble", "NodeSocketFloat", 1.5, 0.0, 20.0),
    ("Hatch Strength", "NodeSocketFloat", 0.85, 0.0, 1.0),
    ("Paper Grain", "NodeSocketFloat", 0.10, 0.0, 1.0),
    ("Aspect", "NodeSocketFloat", 16 / 9, 0.1, 10.0),
]


def build_group():
    old = bpy.data.node_groups.get(GROUP_NAME)
    if old:
        bpy.data.node_groups.remove(old)
    nt = bpy.data.node_groups.new(GROUP_NAME, "ShaderNodeTree")

    for name, stype, default, lo, hi in GROUP_INPUTS:
        s = nt.interface.new_socket(name, in_out="INPUT", socket_type=stype)
        s.default_value = default
        if lo is not None:
            s.min_value, s.max_value = lo, hi
    nt.interface.new_socket("Shader", in_out="OUTPUT", socket_type="NodeSocketShader")
    nt.interface.new_socket("Color", in_out="OUTPUT", socket_type="NodeSocketColor")

    N = nt.nodes
    L = nt.links
    gi = _node(N, "NodeGroupInput", -2200, 0)
    go = _node(N, "NodeGroupOutput", 1900, 0)
    I = gi.outputs

    # ---- 光量（EEVEE の Shader to RGB）----
    diff = _node(N, "ShaderNodeBsdfDiffuse", -1900, 600)
    diff.inputs["Color"].default_value = (1, 1, 1, 1)
    s2rgb = _node(N, "ShaderNodeShaderToRGB", -1700, 600)
    L.new(diff.outputs[0], s2rgb.inputs[0])
    bw = _node(N, "ShaderNodeRGBToBW", -1500, 600)
    L.new(s2rgb.outputs["Color"], bw.inputs[0])
    light = bw.outputs[0]

    # ---- 座標 ----
    tc = _node(N, "ShaderNodeTexCoord", -1900, 0)
    sep = _node(N, "ShaderNodeSeparateXYZ", -1700, -100)
    L.new(tc.outputs["Window"], sep.inputs[0])
    comb = _node(N, "ShaderNodeCombineXYZ", -1300, -100)
    L.new(_math(nt, "MULTIPLY", sep.outputs[0], I["Aspect"], -1500, -60), comb.inputs[0])
    L.new(sep.outputs[1], comb.inputs[1])
    screen = comb.outputs[0]

    # ---- 水彩のムラ（オブジェクトに貼り付く）----
    wash = _node(N, "ShaderNodeTexNoise", -1300, 400)
    wash.inputs["Detail"].default_value = 5.0
    wash.inputs["Roughness"].default_value = 0.6
    L.new(tc.outputs["Object"], wash.inputs["Vector"])
    L.new(I["Wash Scale"], wash.inputs["Scale"])
    wv = _math(nt, "SUBTRACT", wash.outputs["Fac"], 0.5, -1100, 400)
    wv = _math(nt, "MULTIPLY", wv, I["Wash Variation"], -950, 400)
    lw = _math(nt, "ADD", light, wv, -800, 500)   # 揺らいだ光量

    # ---- トーン帯 ----
    soft = I["Band Softness"]
    lt_lo = _math(nt, "SUBTRACT", I["Light Threshold"], soft, -800, 250)
    lt_hi = _math(nt, "ADD", I["Light Threshold"], soft, -800, 150)
    light_mask = _smooth(nt, lw, lt_lo, lt_hi, -600, 300)
    sh_lo = _math(nt, "SUBTRACT", I["Shadow Threshold"], soft, -800, 50)
    sh_hi = _math(nt, "ADD", I["Shadow Threshold"], soft, -800, -50)
    shadow_mask = _smooth(nt, lw, sh_lo, sh_hi, -600, 100, invert=True)

    # ---- 色：褪色した顔料を紙に乗算 ----
    hsv = _node(N, "ShaderNodeHueSaturation", -1300, 900)
    L.new(I["Base Color"], hsv.inputs["Color"])
    L.new(_math(nt, "SUBTRACT", 1.0, I["Desaturate"], -1500, 850), hsv.inputs["Saturation"])
    pigment = _mix(nt, "MIX", 0.2, hsv.outputs[0], I["Shadow Tint"], -1100, 900)  # セピア寄せ
    mid = _mix(nt, "MULTIPLY", 1.0, I["Paper Color"], pigment, -900, 900)
    shadow = _mix(nt, "MULTIPLY", 1.0, mid, I["Shadow Tint"], -700, 900)
    highlight = _mix(nt, "MIX", I["Highlight Wash"], I["Paper Color"], mid, -700, 750)

    col = _mix(nt, "MIX", shadow_mask, mid, shadow, -400, 800)
    col = _mix(nt, "MIX", light_mask, col, highlight, -200, 800)

    # ---- 顔料の粒状感（グラニュレーション）----
    gran = _node(N, "ShaderNodeTexNoise", -600, 1150)
    gran.inputs["Scale"].default_value = 180.0
    gran.inputs["Detail"].default_value = 2.0
    L.new(tc.outputs["Object"], gran.inputs["Vector"])
    gfac = _smooth(nt, gran.outputs["Fac"], 0.45, 0.75, -400, 1150)
    gfac = _math(nt, "MULTIPLY", gfac, I["Granulation"], -200, 1150)
    gfac = _math(nt, "MULTIPLY", gfac, _math(nt, "SUBTRACT", 1.0, light_mask, -200, 1000), 0, 1100)
    col = _mix(nt, "MIX", gfac, col, shadow, 200, 900)

    # ---- 縁の顔料溜まり ----
    lwt = _node(N, "ShaderNodeLayerWeight", -200, 600)
    lwt.inputs["Blend"].default_value = 0.35
    edge = _math(nt, "POWER", lwt.outputs["Facing"], 2.5, 0, 600)
    edge = _math(nt, "MULTIPLY", edge, I["Edge Darkening"], 150, 600, clamp=True)
    col = _mix(nt, "MULTIPLY", edge, col, I["Shadow Tint"], 400, 800)

    # ---- ハッチング（スクリーン空間・揺らぎ付き）----
    def hatch_layer(angle, scale_mul, threshold, y):
        mp = _node(N, "ShaderNodeMapping", -1100, y)
        mp.inputs["Rotation"].default_value = (0.0, 0.0, angle)
        L.new(screen, mp.inputs["Vector"])
        wave = _node(N, "ShaderNodeTexWave", -900, y,
                     wave_type="BANDS", bands_direction="X", wave_profile="SIN")
        wave.inputs["Detail"].default_value = 2.0
        wave.inputs["Detail Scale"].default_value = 1.5
        L.new(mp.outputs[0], wave.inputs["Vector"])
        L.new(_math(nt, "MULTIPLY", I["Hatch Density"], scale_mul, -1100, y - 250),
              wave.inputs["Scale"])
        L.new(I["Hatch Wobble"], wave.inputs["Distortion"])
        w_hi = _math(nt, "ADD", I["Hatch Width"], 0.12, -700, y - 200)
        line = _smooth(nt, wave.outputs["Fac"], I["Hatch Width"], w_hi, -500, y, invert=True)
        t_lo = _math(nt, "SUBTRACT", threshold, 0.04, -700, y - 300)
        t_hi = _math(nt, "ADD", threshold, 0.04, -700, y - 380)
        mask = _smooth(nt, lw, t_lo, t_hi, -500, y - 250, invert=True)
        return _math(nt, "MULTIPLY", line, mask, -300, y)

    h1 = hatch_layer(math.radians(35), 1.0, I["Hatch Threshold"], -500)
    h2 = hatch_layer(math.radians(-50), 1.15, I["Cross Hatch Threshold"], -1000)
    ink = _math(nt, "MAXIMUM", h1, h2, -100, -700)
    ink = _math(nt, "MULTIPLY", ink, I["Hatch Strength"], 100, -700)
    col = _mix(nt, "MIX", ink, col, I["Ink Color"], 700, 600)

    # ---- 紙の繊維とグレイン（スクリーン空間）----
    fine = _node(N, "ShaderNodeTexNoise", 400, -300)
    fine.inputs["Scale"].default_value = 350.0
    fine.inputs["Detail"].default_value = 3.0
    L.new(screen, fine.inputs["Vector"])
    fiber = _node(N, "ShaderNodeTexNoise", 400, -600)
    fiber.inputs["Scale"].default_value = 12.0
    fiber.inputs["Detail"].default_value = 8.0
    L.new(screen, fiber.inputs["Vector"])
    g = _math(nt, "ADD", fine.outputs["Fac"], fiber.outputs["Fac"], 600, -400)
    g = _math(nt, "SUBTRACT", g, 1.0, 750, -400)
    g = _math(nt, "MULTIPLY", g, I["Paper Grain"], 900, -400)
    g = _math(nt, "ADD", g, 1.0, 1050, -400)
    gcol = _node(N, "ShaderNodeCombineColor", 1200, -400)
    for i in range(3):
        L.new(g, gcol.inputs[i])
    col = _mix(nt, "MULTIPLY", 1.0, col, gcol.outputs[0], 1300, 400)

    emit = _node(N, "ShaderNodeEmission", 1600, 200)
    L.new(col, emit.inputs["Color"])
    L.new(emit.outputs[0], go.inputs["Shader"])
    L.new(col, go.inputs["Color"])
    return nt


# --------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------

def _aspect():
    r = bpy.context.scene.render
    return (r.resolution_x * r.pixel_aspect_x) / max(1, r.resolution_y * r.pixel_aspect_y)


def make_material(name, base_color, **overrides):
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    grp = _node(nt.nodes, "ShaderNodeGroup", 0, 0)
    grp.node_tree = bpy.data.node_groups[GROUP_NAME]
    grp.width = 260
    out = _node(nt.nodes, "ShaderNodeOutputMaterial", 400, 0)
    nt.links.new(grp.outputs["Shader"], out.inputs["Surface"])
    grp.inputs["Base Color"].default_value = base_color
    grp.inputs["Aspect"].default_value = _aspect()
    for k, v in overrides.items():
        grp.inputs[k].default_value = v
    return mat


def _base_color_of(mat):
    if mat and mat.use_nodes:
        for n in mat.node_tree.nodes:
            if n.type == "BSDF_PRINCIPLED":
                return tuple(n.inputs["Base Color"].default_value)
            if n.type == "GROUP" and n.node_tree and n.node_tree.name == GROUP_NAME:
                return tuple(n.inputs["Base Color"].default_value)
    if mat:
        return tuple(mat.diffuse_color)
    return (0.6, 0.55, 0.4, 1.0)


def apply_to_objects(objs):
    for ob in objs:
        if ob.type not in {"MESH", "CURVE", "SURFACE", "META", "FONT"}:
            continue
        if not ob.material_slots:
            ob.data.materials.append(make_material(f"Rackham_{ob.name}", _base_color_of(None)))
            continue
        for slot in ob.material_slots:
            src = slot.material
            new_name = src.name if src and src.name.startswith("Rackham_") else \
                f"Rackham_{src.name if src else ob.name}"
            slot.material = make_material(new_name, _base_color_of(src))


# --------------------------------------------------------------------------
# world / render / freestyle
# --------------------------------------------------------------------------

def setup_world(scene):
    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()
    N, L = nt.nodes, nt.links

    tc = _node(N, "ShaderNodeTexCoord", -900, 0)
    noise = _node(N, "ShaderNodeTexNoise", -700, 0)
    noise.inputs["Scale"].default_value = 6.0
    noise.inputs["Detail"].default_value = 8.0
    L.new(tc.outputs["Window"], noise.inputs["Vector"])
    ramp = _node(N, "ShaderNodeValToRGB", -500, 0)
    ramp.color_ramp.elements[0].position = 0.3
    ramp.color_ramp.elements[0].color = (0.80, 0.72, 0.56, 1)   # 黄ばみ・シミ
    ramp.color_ramp.elements[1].position = 0.6
    ramp.color_ramp.elements[1].color = PAPER
    L.new(noise.outputs["Fac"], ramp.inputs["Fac"])

    # カメラには紙、ライティングには弱い暖色の環境光
    lp = _node(N, "ShaderNodeLightPath", -300, 300)
    bg_cam = _node(N, "ShaderNodeBackground", -200, 0)
    L.new(ramp.outputs["Color"], bg_cam.inputs["Color"])
    bg_amb = _node(N, "ShaderNodeBackground", -200, -200)
    bg_amb.inputs["Color"].default_value = (0.9, 0.82, 0.65, 1)
    bg_amb.inputs["Strength"].default_value = 0.25
    mix = _node(N, "ShaderNodeMixShader", 0, 0)
    L.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
    L.new(bg_amb.outputs[0], mix.inputs[1])
    L.new(bg_cam.outputs[0], mix.inputs[2])
    out = _node(N, "ShaderNodeOutputWorld", 200, 0)
    L.new(mix.outputs[0], out.inputs["Surface"])


def setup_freestyle(scene):
    scene.render.use_freestyle = True
    scene.render.line_thickness_mode = "RELATIVE"
    vl = bpy.context.view_layer
    fs = vl.freestyle_settings
    fs.mode = "EDITOR"
    fs.crease_angle = math.radians(130)
    fs.use_smoothness = True

    ls_set = fs.linesets[0] if len(fs.linesets) else fs.linesets.new("LineSet")
    ls_set.select_by_visibility = True
    ls_set.visibility = "VISIBLE"
    ls_set.select_by_edge_types = True
    ls_set.select_silhouette = True
    ls_set.select_border = True
    ls_set.select_crease = True
    ls_set.select_external_contour = True
    ls_set.select_contour = True

    old = bpy.data.linestyles.get(LINESTYLE_NAME)
    if old:
        bpy.data.linestyles.remove(old)
    st = bpy.data.linestyles.new(LINESTYLE_NAME)
    ls_set.linestyle = st
    st.color = INK[:3]
    st.thickness = 2.0
    st.thickness_position = "CENTER"
    st.caps = "ROUND"
    st.chaining = "PLAIN"
    st.use_same_object = True

    # 手描きの揺らぎ・線のはみ出し
    m = st.geometry_modifiers.new("Sampling", "SAMPLING")
    m.sampling = 2.0
    m = st.geometry_modifiers.new("Overshoot", "BACKBONE_STRETCHER")
    m.backbone_length = 4.0
    m = st.geometry_modifiers.new("Wobble", "PERLIN_NOISE_1D")
    m.frequency = 12.0
    m.amplitude = 1.6
    m.octaves = 3
    m = st.geometry_modifiers.new("Tremor", "SINUS_DISPLACEMENT")
    m.wavelength = 18.0
    m.amplitude = 0.6

    # ペン先の強弱：入り抜き＋カリグラフィ＋ノイズ
    m = st.thickness_modifiers.new("Taper", "ALONG_STROKE")
    m.mapping = "CURVE"
    m.blend = "MULTIPLY"
    m.value_min, m.value_max = 0.35, 1.0
    cm = m.curve
    c = cm.curves[0]
    c.points[0].location = (0.0, 0.0)
    c.points[1].location = (1.0, 0.0)
    c.points.new(0.25, 1.0)
    c.points.new(0.75, 0.9)
    cm.update()
    m = st.thickness_modifiers.new("Nib", "CALLIGRAPHY")
    m.blend = "MULTIPLY"
    m.orientation = math.radians(35)
    m.thickness_min, m.thickness_max = 0.6, 1.4
    m = st.thickness_modifiers.new("Pressure", "NOISE")
    m.blend = "ADD"
    m.amplitude = 0.8
    m.period = 20.0
    m = st.alpha_modifiers.new("Ink Flow", "NOISE")
    m.blend = "ADD"
    m.amplitude = 0.3
    m.period = 40.0


def setup_render(scene):
    engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    for e in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        if e in engines:
            scene.render.engine = e
            break
    # 色をそのまま出す（AgX/Filmic だと紙色が濁る）
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.render.film_transparent = False


def setup_scene(scene=None):
    scene = scene or bpy.context.scene
    setup_render(scene)
    setup_world(scene)
    setup_freestyle(scene)


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------

def build_demo():
    scene = bpy.context.scene
    for ob in list(scene.objects):
        bpy.data.objects.remove(ob, do_unlink=True)

    def add(ob, color, **ov):
        ob.data.materials.clear()
        ob.data.materials.append(make_material(f"Rackham_{ob.name}", color, **ov))
        return ob

    # 地面
    bpy.ops.mesh.primitive_plane_add(size=40)
    ground = bpy.context.object
    ground.name = "Ground"
    sub = ground.modifiers.new("Sub", "SUBSURF")
    sub.subdivision_type = "SIMPLE"
    sub.levels = sub.render_levels = 6
    tex = bpy.data.textures.new("GroundBumps", "CLOUDS")
    tex.noise_scale = 2.5
    d = ground.modifiers.new("Bumps", "DISPLACE")
    d.texture = tex
    d.strength = 0.6
    add(ground, (0.50, 0.46, 0.28, 1), **{"Wash Scale": 0.6})

    # ねじれた古木
    bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.55, depth=6, end_fill_type="TRIFAN",
                                        location=(-1.2, 1.5, 3))
    trunk = bpy.context.object
    trunk.name = "GnarledTree"
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.subdivide(number_cuts=12)
    bpy.ops.object.mode_set(mode="OBJECT")
    tex2 = bpy.data.textures.new("Bark", "DISTORTED_NOISE")
    tex2.noise_scale = 0.6
    tex2.distortion = 2.0
    d = trunk.modifiers.new("Gnarl", "DISPLACE")
    d.texture = tex2
    d.strength = 0.18
    tw = trunk.modifiers.new("Twist", "SIMPLE_DEFORM")
    tw.deform_method = "TWIST"
    tw.angle = math.radians(120)
    tp = trunk.modifiers.new("Taper", "SIMPLE_DEFORM")
    tp.deform_method = "TAPER"
    tp.factor = -0.12
    s = trunk.modifiers.new("Smooth", "SUBSURF")
    s.levels = s.render_levels = 1
    add(trunk, (0.38, 0.30, 0.20, 1), **{"Edge Darkening": 0.6})

    # 妖精 / ゴブリン役
    bpy.ops.mesh.primitive_monkey_add(location=(1.4, -0.6, 0.9), rotation=(0, 0, math.radians(25)))
    imp = bpy.context.object
    imp.name = "Goblin"
    s = imp.modifiers.new("Sub", "SUBSURF")
    s.levels = s.render_levels = 2
    add(imp, (0.55, 0.62, 0.42, 1))

    # 岩
    for i, (x, y, r) in enumerate([(3.2, 1.5, 0.9), (-3.5, -1.0, 0.6), (0.4, 2.8, 0.5)]):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=r, location=(x, y, r * 0.4))
        rock = bpy.context.object
        rock.name = f"Rock{i}"
        rock.scale.z = 0.7
        bpy.ops.object.shade_smooth()
        d = rock.modifiers.new("Rough", "DISPLACE")
        d.texture = tex
        d.strength = 0.25
        add(rock, (0.52, 0.50, 0.44, 1))

    # 光とカメラ
    bpy.ops.object.light_add(type="SUN", rotation=(math.radians(50), math.radians(10), math.radians(-35)))
    sun = bpy.context.object
    sun.data.energy = 3.5
    sun.data.color = (1.0, 0.95, 0.85)
    sun.data.angle = math.radians(3)

    bpy.ops.object.camera_add(location=(7.5, -9.5, 4.2))
    cam = bpy.context.object
    cam.data.lens = 40
    track = cam.constraints.new("TRACK_TO")
    target = bpy.data.objects.new("CamTarget", None)
    scene.collection.objects.link(target)
    target.location = (0, 0.5, 1.6)
    track.target = target
    scene.camera = cam


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------

def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    demo = "--demo" in argv
    render_path = argv[argv.index("--render") + 1] if "--render" in argv else None

    scene = bpy.context.scene
    build_group()
    selected = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if demo or (not selected and not bpy.app.background):
        build_demo()
    else:
        apply_to_objects(selected or [o for o in scene.objects if o.type == "MESH"])
    setup_scene(scene)

    if render_path:
        scene.render.filepath = render_path
        bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
