"""
Deep Reef — 深海の発光サンゴ礁
================================

A_life のテーマ（サンゴの成長・群体・生命の自己組織化）を Blender で立体化した
プロシージャルなシーンです。すべてコードから生成されるので、外部アセットは不要です。

  * 分岐成長アルゴリズムで育つ枝サンゴ（Skin モディファイア）
  * ボロノイ模様の脳サンゴ / 岩 / うねる砂地
  * 先端が発光するポリプ、脈打ちながら漂うクラゲ
  * ブラウン運動するプランクトン粒子
  * ボリュームフォグと上方からの光芒（ゴッドレイ）
  * 240 フレームのカメラドリー（ループ可能なクラゲの拍動）

使い方
------
Blender 4.2 以降（4.2 で動作確認）:

    # GUI: Scripting タブでこのファイルを開いて「スクリプト実行」
    # ヘッドレスで .blend を保存:
    blender -b -P deep_reef.py -- --save deep_reef.blend
    # 静止画をレンダリング (フレーム 120):
    blender -b -P deep_reef.py -- --render reef.png --frame 120
    # オプション: --seed 7  --samples 128  --res 1920 1080
"""

import math
import random
import sys

import bpy
from mathutils import Vector, noise

# ---------------------------------------------------------------------------
# 引数
# ---------------------------------------------------------------------------

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {"seed": 42, "samples": 96, "res": (1280, 720),
            "render": None, "save": None, "frame": 120}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--seed":
            opts["seed"] = int(argv[i + 1]); i += 1
        elif a == "--samples":
            opts["samples"] = int(argv[i + 1]); i += 1
        elif a == "--res":
            opts["res"] = (int(argv[i + 1]), int(argv[i + 2])); i += 2
        elif a == "--render":
            opts["render"] = argv[i + 1]; i += 1
        elif a == "--save":
            opts["save"] = argv[i + 1]; i += 1
        elif a == "--frame":
            opts["frame"] = int(argv[i + 1]); i += 1
        i += 1
    return opts


OPTS = parse_args()
rng = random.Random(OPTS["seed"])

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for coll in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                 bpy.data.lights, bpy.data.cameras, bpy.data.textures):
        for block in list(coll):
            coll.remove(block)


def collection(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def link(obj, coll):
    coll.objects.link(obj)
    return obj


def mesh_object(name, verts, edges=(), faces=(), coll=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(v) for v in verts], list(edges), list(faces))
    me.update()
    ob = bpy.data.objects.new(name, me)
    link(ob, coll or bpy.context.scene.collection)
    return ob


def node_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    return m, nt, out


def hsv(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (r, g, b, 1.0)


def smooth(ob):
    for p in ob.data.polygons:
        p.use_smooth = True


# ---------------------------------------------------------------------------
# マテリアル
# ---------------------------------------------------------------------------

def mat_coral(name, base, tip, glow):
    """高さ（Generated Z）で根元→先端のグラデーション。先端ほど発光する。"""
    m, nt, out = node_mat(name)
    N, L = nt.nodes, nt.links
    tc = N.new("ShaderNodeTexCoord"); tc.location = (-900, 0)
    sep = N.new("ShaderNodeSeparateXYZ"); sep.location = (-700, 0)
    ramp = N.new("ShaderNodeValToRGB"); ramp.location = (-500, 100)
    ramp.color_ramp.elements[0].color = base
    ramp.color_ramp.elements[1].color = tip
    ramp.color_ramp.elements[0].position = 0.15
    glow_ramp = N.new("ShaderNodeMapRange"); glow_ramp.location = (-500, -200)
    glow_ramp.inputs["From Min"].default_value = 0.55
    glow_ramp.inputs["From Max"].default_value = 1.0
    glow_ramp.inputs["To Max"].default_value = glow
    nz = N.new("ShaderNodeTexNoise"); nz.location = (-500, -450)
    nz.inputs["Scale"].default_value = 40.0
    bump = N.new("ShaderNodeBump"); bump.location = (-200, -450)
    bump.inputs["Strength"].default_value = 0.25
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (100, 0)
    bsdf.inputs["Roughness"].default_value = 0.55
    bsdf.inputs["Subsurface Weight"].default_value = 0.35
    bsdf.inputs["Subsurface Radius"].default_value = (0.3, 0.15, 0.1)
    bsdf.inputs["Subsurface Scale"].default_value = 0.05
    L.new(tc.outputs["Generated"], sep.inputs[0])
    L.new(sep.outputs["Z"], ramp.inputs["Fac"])
    L.new(sep.outputs["Z"], glow_ramp.inputs["Value"])
    L.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(ramp.outputs["Color"], bsdf.inputs["Emission Color"])
    L.new(glow_ramp.outputs["Result"], bsdf.inputs["Emission Strength"])
    L.new(nz.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


def mat_emit(name, color, strength):
    m, nt, out = node_mat(name)
    e = nt.nodes.new("ShaderNodeEmission")
    e.inputs["Color"].default_value = color
    e.inputs["Strength"].default_value = strength
    nt.links.new(e.outputs[0], out.inputs["Surface"])
    return m


def mat_jelly(name, color):
    """縁が光る半透明のクラゲ。"""
    m, nt, out = node_mat(name)
    N, L = nt.nodes, nt.links
    lw = N.new("ShaderNodeLayerWeight"); lw.location = (-600, 200)
    lw.inputs["Blend"].default_value = 0.35
    pw = N.new("ShaderNodeMath"); pw.operation = "POWER"; pw.location = (-400, 200)
    pw.inputs[1].default_value = 1.6
    mul = N.new("ShaderNodeMath"); mul.operation = "MULTIPLY"; mul.location = (-200, 200)
    mul.inputs[1].default_value = 9.0
    glass = N.new("ShaderNodeBsdfPrincipled"); glass.location = (-200, -100)
    glass.inputs["Base Color"].default_value = color
    glass.inputs["Transmission Weight"].default_value = 1.0
    glass.inputs["Roughness"].default_value = 0.15
    glass.inputs["IOR"].default_value = 1.05
    glass.inputs["Emission Color"].default_value = color
    L.new(lw.outputs["Facing"], pw.inputs[0])
    L.new(pw.outputs[0], mul.inputs[0])
    L.new(mul.outputs[0], glass.inputs["Emission Strength"])
    transp = N.new("ShaderNodeBsdfTransparent"); transp.location = (100, 150)
    mix = N.new("ShaderNodeMixShader"); mix.location = (350, 0)
    mix.inputs["Fac"].default_value = 0.6
    L.new(transp.outputs[0], mix.inputs[1])
    L.new(glass.outputs[0], mix.inputs[2])
    L.new(mix.outputs[0], out.inputs["Surface"])
    m.blend_method = "BLEND"
    return m


def mat_sand():
    m, nt, out = node_mat("Sand")
    N, L = nt.nodes, nt.links
    nz = N.new("ShaderNodeTexNoise"); nz.location = (-700, 0)
    nz.inputs["Scale"].default_value = 3.0
    nz.inputs["Detail"].default_value = 8.0
    ramp = N.new("ShaderNodeValToRGB"); ramp.location = (-450, 100)
    ramp.color_ramp.elements[0].color = (0.035, 0.05, 0.07, 1)
    ramp.color_ramp.elements[1].color = (0.16, 0.17, 0.16, 1)
    fine = N.new("ShaderNodeTexNoise"); fine.location = (-700, -300)
    fine.inputs["Scale"].default_value = 300.0
    bump = N.new("ShaderNodeBump"); bump.location = (-300, -300)
    bump.inputs["Strength"].default_value = 0.15
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (100, 0)
    bsdf.inputs["Roughness"].default_value = 0.95
    L.new(nz.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(fine.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


def mat_brain(name, a, b):
    """ボロノイの縁で溝を刻んだ脳サンゴ。"""
    m, nt, out = node_mat(name)
    N, L = nt.nodes, nt.links
    vor = N.new("ShaderNodeTexVoronoi"); vor.location = (-700, 0)
    vor.feature = "DISTANCE_TO_EDGE"
    vor.inputs["Scale"].default_value = 9.0
    ramp = N.new("ShaderNodeValToRGB"); ramp.location = (-450, 150)
    ramp.color_ramp.elements[0].color = a
    ramp.color_ramp.elements[1].color = b
    ramp.color_ramp.elements[1].position = 0.12
    bump = N.new("ShaderNodeBump"); bump.location = (-300, -250)
    bump.inputs["Strength"].default_value = 0.8
    edge = N.new("ShaderNodeMapRange"); edge.location = (-450, -100)
    edge.inputs["From Min"].default_value = 0.04
    edge.inputs["From Max"].default_value = 0.0
    edge.inputs["To Max"].default_value = 2.5
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (100, 0)
    bsdf.inputs["Roughness"].default_value = 0.6
    bsdf.inputs["Subsurface Weight"].default_value = 0.2
    bsdf.inputs["Emission Color"].default_value = b
    L.new(vor.outputs["Distance"], ramp.inputs["Fac"])
    L.new(vor.outputs["Distance"], bump.inputs["Height"])
    L.new(vor.outputs["Distance"], edge.inputs["Value"])
    L.new(edge.outputs["Result"], bsdf.inputs["Emission Strength"])
    L.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


def mat_rock():
    m, nt, out = node_mat("Rock")
    N, L = nt.nodes, nt.links
    nz = N.new("ShaderNodeTexNoise"); nz.location = (-600, 0)
    nz.inputs["Scale"].default_value = 6.0
    nz.inputs["Detail"].default_value = 10.0
    ramp = N.new("ShaderNodeValToRGB"); ramp.location = (-350, 100)
    ramp.color_ramp.elements[0].color = (0.015, 0.02, 0.025, 1)
    ramp.color_ramp.elements[1].color = (0.09, 0.1, 0.1, 1)
    bump = N.new("ShaderNodeBump"); bump.location = (-200, -200)
    bsdf = N.new("ShaderNodeBsdfPrincipled"); bsdf.location = (100, 0)
    bsdf.inputs["Roughness"].default_value = 0.9
    L.new(nz.outputs["Fac"], ramp.inputs["Fac"])
    L.new(nz.outputs["Fac"], bump.inputs["Height"])
    L.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
    L.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    L.new(bsdf.outputs[0], out.inputs["Surface"])
    return m


def mat_water_volume():
    m, nt, out = node_mat("WaterVolume")
    N, L = nt.nodes, nt.links
    vol = N.new("ShaderNodeVolumePrincipled"); vol.location = (200, 0)
    vol.inputs["Color"].default_value = (0.25, 0.65, 0.9, 1)
    vol.inputs["Density"].default_value = 0.045
    vol.inputs["Anisotropy"].default_value = 0.6
    vol.inputs["Absorption Color"].default_value = (0.05, 0.35, 0.6, 1)
    L.new(vol.outputs[0], out.inputs["Volume"])
    return m


# ---------------------------------------------------------------------------
# 地形
# ---------------------------------------------------------------------------

def seabed_height(x, y):
    h = 0.6 * noise.noise(Vector((x * 0.12, y * 0.12, 0.3)))
    h += 0.15 * noise.noise(Vector((x * 0.5, y * 0.5, 1.7)))
    # 砂紋
    h += 0.03 * math.sin(x * 2.2 + 1.5 * noise.noise(Vector((x * 0.3, y * 0.3, 5.0))))
    return h


def build_seabed(coll):
    n, size = 160, 40.0
    verts, faces = [], []
    for j in range(n + 1):
        for i in range(n + 1):
            x = (i / n - 0.5) * size
            y = (j / n - 0.5) * size
            verts.append((x, y, seabed_height(x, y)))
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces.append((a, a + 1, a + n + 2, a + n + 1))
    ob = mesh_object("Seabed", verts, faces=faces, coll=coll)
    smooth(ob)
    ob.data.materials.append(mat_sand())
    return ob


def build_rock(name, pos, scale, mat, coll):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=4, radius=1.0)
    ob = bpy.context.active_object
    for c in ob.users_collection:
        c.objects.unlink(ob)
    link(ob, coll)
    ob.name = name
    seed = Vector((rng.random() * 100, rng.random() * 100, rng.random() * 100))
    for v in ob.data.vertices:
        d = noise.fractal(v.co * 1.3 + seed, 0.5, 2.0, 4)
        v.co *= 1.0 + 0.35 * d
    ob.location = pos
    ob.scale = scale
    ob.rotation_euler = (rng.random(), rng.random(), rng.random() * 6.28)
    smooth(ob)
    ob.data.materials.append(mat)
    return ob


# ---------------------------------------------------------------------------
# 枝サンゴ：確率的な分岐成長
# ---------------------------------------------------------------------------

def grow_coral(name, origin, mat, polyp_mat, coll, max_depth=6,
               spread=0.55, length=0.45, thickness=0.09):
    verts, edges, radii, tips = [], [], [], []

    def add(p, r):
        verts.append(p.copy())
        radii.append(r)
        return len(verts) - 1

    root = add(Vector((0, 0, -0.2)), thickness * 1.2)

    def branch(parent, pos, direction, depth, r):
        steps = rng.randint(2, 4)
        cur, p = parent, pos.copy()
        for s in range(steps):
            # 上方への走光性 + ゆらぎ
            jitter = Vector((rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))) * 0.18
            direction = (direction + Vector((0, 0, 0.12)) + jitter).normalized()
            p = p + direction * length * (0.8 ** (max_depth - depth)) * 0.42
            r *= 0.92
            nxt = add(p, max(r, 0.012))
            edges.append((cur, nxt))
            cur = nxt
        if depth <= 0 or r < 0.015:
            tips.append(p.copy())
            return
        n_children = rng.choice((2, 2, 2, 3))
        for k in range(n_children):
            ang = 2 * math.pi * k / n_children + rng.random()
            side = Vector((math.cos(ang), math.sin(ang), 0)) * spread
            branch(cur, p, (direction + side).normalized(), depth - 1, r * 0.82)

    branch(root, Vector((0, 0, -0.2)), Vector((0, 0, 1)), max_depth, thickness)

    ob = mesh_object(name, verts, edges=edges, coll=coll)
    ob.location = origin
    sk = ob.modifiers.new("Skin", "SKIN")
    sk.use_smooth_shade = True
    for sv, r in zip(ob.data.skin_vertices[0].data, radii):
        sv.radius = (r, r)
    ob.data.skin_vertices[0].data[0].use_root = True
    sub = ob.modifiers.new("Subsurf", "SUBSURF")
    sub.levels, sub.render_levels = 1, 2
    ob.data.materials.append(mat)
    ob.rotation_euler.z = rng.random() * 6.28

    # 先端のポリプ（発光）
    if tips:
        pv, pf = [], []
        for t in tips:
            if rng.random() < 0.7:
                base = len(pv)
                s = rng.uniform(0.03, 0.055)
                # 小さな八面体
                for d in ((s, 0, 0), (-s, 0, 0), (0, s, 0), (0, -s, 0), (0, 0, s * 1.4), (0, 0, -s)):
                    pv.append(t + Vector(d))
                for f in ((0, 2, 4), (2, 1, 4), (1, 3, 4), (3, 0, 4),
                          (2, 0, 5), (1, 2, 5), (3, 1, 5), (0, 3, 5)):
                    pf.append(tuple(base + i for i in f))
        pol = mesh_object(name + "_polyps", pv, faces=pf, coll=coll)
        pol.parent = ob
        pol.data.materials.append(polyp_mat)
        ss = pol.modifiers.new("Subsurf", "SUBSURF")
        ss.levels, ss.render_levels = 1, 2
    return ob


# ---------------------------------------------------------------------------
# クラゲ
# ---------------------------------------------------------------------------

def build_jellyfish(name, pos, size, color, coll, frame_end, phase):
    mat = mat_jelly(name + "_mat", color)
    root = bpy.data.objects.new(name, None)
    link(root, coll)
    root.location = pos

    # 傘：半球をフリル状に変形
    seg, rings = 48, 16
    verts, faces = [], []
    for r in range(rings + 1):
        th = (r / rings) * (math.pi * 0.55)
        for s in range(seg):
            ph = 2 * math.pi * s / seg
            frill = 1.0 + (0.06 * math.sin(ph * 8) if r > rings - 3 else 0)
            rad = math.sin(th) * frill
            verts.append((rad * math.cos(ph), rad * math.sin(ph), math.cos(th) * 0.75))
    for r in range(rings):
        for s in range(seg):
            a = r * seg + s
            b = r * seg + (s + 1) % seg
            faces.append((a, b, b + seg, a + seg))
    bell = mesh_object(name + "_bell", verts, faces=faces, coll=coll)
    bell.parent = root
    bell.scale = (size, size, size)
    smooth(bell)
    bell.modifiers.new("Solidify", "SOLIDIFY").thickness = 0.04
    sub = bell.modifiers.new("Subsurf", "SUBSURF")
    sub.levels, sub.render_levels = 1, 2
    bell.data.materials.append(mat)

    # 触手：頂点の鎖 → 移動に合わせて揺れる Displace → Skin で管にする
    tex = bpy.data.textures.new(name + "_flow", "CLOUDS")
    tex.noise_scale = 0.8
    for k in range(10):
        ph = 2 * math.pi * k / 10 + rng.random() * 0.3
        npts = 24
        rr = 0.75 * size * rng.uniform(0.7, 1.0)
        ln = size * rng.uniform(2.5, 4.5)
        pts = []
        for i in range(npts):
            t = i / (npts - 1)
            wob = 0.12 * size * math.sin(t * 7 + k)
            pts.append(((rr * (1 - 0.4 * t) + wob) * math.cos(ph),
                        (rr * (1 - 0.4 * t) + wob) * math.sin(ph),
                        0.1 * size - ln * t))
        tob = mesh_object(name + f"_tentacle{k}", pts,
                          edges=[(i, i + 1) for i in range(npts - 1)], coll=coll)
        tob.parent = root
        for axis in ("X", "Y"):
            d = tob.modifiers.new("Sway" + axis, "DISPLACE")
            d.texture = tex
            d.texture_coords = "GLOBAL"
            d.direction = axis
            d.mid_level = 0.5
            d.strength = 0.5 * size
        sk = tob.modifiers.new("Skin", "SKIN")
        sk.use_smooth_shade = True
        for i, sv in enumerate(tob.data.skin_vertices[0].data):
            r = 0.014 * size * (1 - 0.8 * i / (npts - 1))
            sv.radius = (r, r)
        tob.data.skin_vertices[0].data[0].use_root = True
        tob.data.materials.append(mat)

    # 拍動：傘を収縮させるキーフレーム（周期 48 フレーム、ループ）
    period = 48
    for f in range(1, frame_end + period, period // 4):
        t = ((f + phase) % period) / period
        squeeze = 0.5 - 0.5 * math.cos(2 * math.pi * t)
        bell.scale = (size * (1 - 0.18 * squeeze), size * (1 - 0.18 * squeeze),
                      size * (1 + 0.15 * squeeze))
        bell.keyframe_insert("scale", frame=f)

    # 漂流：ゆっくり上昇しながら旋回
    for f in (1, frame_end // 2, frame_end):
        t = f / frame_end
        root.location = (pos[0] + 0.6 * math.sin(t * 3 + phase),
                         pos[1] + 0.4 * math.cos(t * 2 + phase),
                         pos[2] + 1.2 * t)
        root.rotation_euler = (0.15 * math.sin(t * 4 + phase), 0.1 * math.cos(t * 3), t * 0.8)
        root.keyframe_insert("location", frame=f)
        root.keyframe_insert("rotation_euler", frame=f)
    return root


# ---------------------------------------------------------------------------
# プランクトン（パーティクル）
# ---------------------------------------------------------------------------

def build_plankton(coll, frame_end):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0, location=(0, 0, -50))
    mote = bpy.context.active_object
    mote.name = "PlanktonMote"
    for c in mote.users_collection:
        c.objects.unlink(mote)
    link(mote, coll)
    mote.data.materials.append(mat_emit("Plankton", (0.4, 1.0, 0.9, 1), 12.0))
    mote.hide_render = True
    mote.hide_viewport = True

    emitter = mesh_object("PlanktonCloud",
                          [(x, y, z) for x in (-9, 9) for y in (-9, 9) for z in (0.2, 7)],
                          faces=[(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                                 (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)], coll=coll)
    emitter.location = (0, 2, 0)
    emitter.display_type = "WIRE"
    emitter.hide_render = True
    emitter.show_instancer_for_render = False
    ps_mod = emitter.modifiers.new("Plankton", "PARTICLE_SYSTEM")
    ps = ps_mod.particle_system.settings
    ps.count = 1800
    ps.frame_start = ps.frame_end = 1
    ps.lifetime = frame_end + 10
    ps.emit_from = "VOLUME"
    ps.distribution = "RAND"
    ps.normal_factor = 0.0
    ps.factor_random = 0.05
    ps.brownian_factor = 0.15
    ps.effector_weights.gravity = 0.0
    ps.drag_factor = 0.3
    ps.render_type = "OBJECT"
    ps.instance_object = mote
    ps.particle_size = 0.02
    ps.size_random = 0.7
    ps_mod.particle_system.seed = OPTS["seed"]
    return emitter


# ---------------------------------------------------------------------------
# 光・カメラ・環境
# ---------------------------------------------------------------------------

def build_environment(coll):
    world = bpy.data.worlds.new("Abyss")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    bg = nt.nodes["Background"]
    bg.inputs["Color"].default_value = (0.002, 0.012, 0.03, 1)
    bg.inputs["Strength"].default_value = 1.0

    # 水のボリューム（カメラも中に入る大きさ）
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 6))
    water = bpy.context.active_object
    for c in water.users_collection:
        c.objects.unlink(water)
    link(water, coll)
    water.name = "WaterVolume"
    water.scale = (40, 40, 14)
    water.data.materials.append(mat_water_volume())
    water.display_type = "BOUNDS"

    # 水面から射し込む光芒
    sun = bpy.data.lights.new("SurfaceShaft", "SPOT")
    sun.energy = 90000
    sun.color = (0.55, 0.85, 1.0)
    sun.spot_size = math.radians(38)
    sun.spot_blend = 0.6
    sun.shadow_soft_size = 0.3
    so = bpy.data.objects.new("SurfaceShaft", sun)
    link(so, coll)
    so.location = (-3, 6, 13)
    so.rotation_euler = (math.radians(-12), math.radians(-14), 0)

    # 光芒をまだらにするコースティクス風ゴボ
    sun.use_nodes = True
    lnt = sun.node_tree
    em = lnt.nodes["Emission"]
    vor = lnt.nodes.new("ShaderNodeTexVoronoi")
    vor.feature = "SMOOTH_F1"
    vor.inputs["Scale"].default_value = 18.0
    tc = lnt.nodes.new("ShaderNodeTexCoord")
    mr = lnt.nodes.new("ShaderNodeMapRange")
    mr.inputs["From Min"].default_value = 0.1
    mr.inputs["From Max"].default_value = 0.6
    mr.inputs["To Min"].default_value = 0.25
    mr.inputs["To Max"].default_value = 1.6
    lnt.links.new(tc.outputs["Normal"], vor.inputs["Vector"])
    lnt.links.new(vor.outputs["Distance"], mr.inputs["Value"])
    lnt.links.new(mr.outputs["Result"], em.inputs["Strength"])

    # フィルライト
    fill = bpy.data.lights.new("AbyssFill", "AREA")
    fill.energy = 400
    fill.size = 12
    fill.color = (0.2, 0.4, 1.0)
    fo = bpy.data.objects.new("AbyssFill", fill)
    link(fo, coll)
    fo.location = (6, -8, 6)
    fo.rotation_euler = (math.radians(60), 0, math.radians(35))

    # 背面のリムライト（シルエットを立てる）
    rim = bpy.data.lights.new("Rim", "AREA")
    rim.energy = 900
    rim.size = 6
    rim.color = (1.0, 0.35, 0.7)
    ro = bpy.data.objects.new("Rim", rim)
    link(ro, coll)
    ro.location = (0, 12, 3)
    ro.rotation_euler = (math.radians(-80), 0, 0)


def build_camera(coll, frame_end):
    cam = bpy.data.cameras.new("Camera")
    cam.lens = 32
    cam.dof.use_dof = True
    cam.dof.aperture_fstop = 2.8
    co = bpy.data.objects.new("Camera", cam)
    link(co, coll)
    bpy.context.scene.camera = co

    focus = bpy.data.objects.new("Focus", None)
    link(focus, coll)
    focus.location = (0, 1.5, 1.4)
    cam.dof.focus_object = focus

    track = co.constraints.new("TRACK_TO")
    track.target = focus
    track.track_axis = "TRACK_NEGATIVE_Z"
    track.up_axis = "UP_Y"

    # ゆるい弧を描くドリー
    keys = [(1, (-6.0, -7.0, 2.4)), (frame_end // 2, (-1.0, -7.8, 2.8)),
            (frame_end, (4.5, -6.5, 3.3))]
    for f, p in keys:
        co.location = p
        co.keyframe_insert("location", frame=f)
    for fc in co.animation_data.action.fcurves:
        for kp in fc.keyframe_points:
            kp.interpolation = "BEZIER"
            kp.easing = "EASE_IN_OUT"
    focus.location = (0, 1.5, 1.4)
    focus.keyframe_insert("location", frame=1)
    focus.location = (1.0, 1.0, 1.8)
    focus.keyframe_insert("location", frame=frame_end)
    return co


def setup_render(frame_end):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.samples = OPTS["samples"]
    sc.cycles.use_denoising = True
    sc.cycles.volume_step_rate = 4.0
    sc.cycles.max_bounces = 6
    sc.cycles.transparent_max_bounces = 16
    sc.render.resolution_x, sc.render.resolution_y = OPTS["res"]
    sc.render.resolution_percentage = 100
    sc.frame_start, sc.frame_end = 1, frame_end
    sc.render.fps = 24
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Punchy"
    sc.view_settings.exposure = 0.3

    # コンポジット：グレア（ブルーム） + ビネット風レンズ歪み
    sc.use_nodes = True
    nt = sc.node_tree
    nt.nodes.clear()
    rl = nt.nodes.new("CompositorNodeRLayers")
    glare = nt.nodes.new("CompositorNodeGlare")
    glare.glare_type = "FOG_GLOW"
    glare.quality = "HIGH"
    glare.size = 8
    glare.threshold = 0.6
    lens = nt.nodes.new("CompositorNodeLensdist")
    lens.inputs["Dispersion"].default_value = 0.012
    comp = nt.nodes.new("CompositorNodeComposite")
    nt.links.new(rl.outputs["Image"], glare.inputs["Image"])
    nt.links.new(glare.outputs["Image"], lens.inputs["Image"])
    nt.links.new(lens.outputs["Image"], comp.inputs["Image"])


# ---------------------------------------------------------------------------
# 組み立て
# ---------------------------------------------------------------------------

def main():
    frame_end = 240
    reset_scene()
    env_c = collection("Environment")
    reef_c = collection("Reef")
    life_c = collection("Drifters")
    cam_c = collection("Camera")

    build_environment(env_c)
    build_seabed(env_c)

    rock_mat = mat_rock()
    for i in range(9):
        x, y = rng.uniform(-9, 9), rng.uniform(4, 12)
        s = rng.uniform(0.6, 1.8)
        build_rock(f"Rock{i}", (x, y, seabed_height(x, y) - 0.2 * s),
                   (s * rng.uniform(1, 1.6), s, s * rng.uniform(0.5, 0.9)), rock_mat, env_c)

    palettes = [
        ((0.25, 0.03, 0.12, 1), (1.0, 0.35, 0.55, 1), 3.0, (1.0, 0.5, 0.75, 1)),   # 桃
        ((0.02, 0.1, 0.25, 1), (0.2, 0.9, 1.0, 1), 4.0, (0.4, 1.0, 1.0, 1)),       # 青緑
        ((0.25, 0.1, 0.01, 1), (1.0, 0.65, 0.15, 1), 2.5, (1.0, 0.8, 0.3, 1)),     # 橙
        ((0.1, 0.02, 0.25, 1), (0.65, 0.35, 1.0, 1), 3.5, (0.8, 0.6, 1.0, 1)),     # 紫
    ]
    mats = [(mat_coral(f"Coral{i}", b, t, g), mat_emit(f"Polyp{i}", pc, 18.0))
            for i, (b, t, g, pc) in enumerate(palettes)]

    spots = []
    for i in range(22):
        for _ in range(50):
            x, y = rng.uniform(-6.5, 6.5), rng.uniform(-3.0, 7.5)
            if all((x - a) ** 2 + (y - b) ** 2 > 1.2 ** 2 for a, b in spots):
                break
        spots.append((x, y))
        cm, pm = mats[i % len(mats)]
        grow_coral(f"Coral{i}", (x, y, seabed_height(x, y)), cm, pm, reef_c,
                   max_depth=rng.choice((4, 5, 5, 6)),
                   spread=rng.uniform(0.6, 1.0),
                   length=rng.uniform(0.35, 0.55),
                   thickness=rng.uniform(0.07, 0.11))

    brain_mats = [mat_brain("BrainA", (0.05, 0.12, 0.04, 1), (0.6, 1.0, 0.4, 1)),
                  mat_brain("BrainB", (0.2, 0.06, 0.02, 1), (1.0, 0.5, 0.2, 1))]
    for i in range(6):
        x, y = rng.uniform(-7, 7), rng.uniform(-4, 8)
        s = rng.uniform(0.4, 0.8)
        b = build_rock(f"Brain{i}", (x, y, seabed_height(x, y) - 0.1), (s, s, s * 0.65),
                       brain_mats[i % 2], reef_c)
        b.data.materials.clear()
        b.data.materials.append(brain_mats[i % 2])
        b.modifiers.new("Subsurf", "SUBSURF").render_levels = 2

    jelly_colors = [(0.5, 0.8, 1.0, 1), (1.0, 0.55, 0.85, 1), (0.6, 1.0, 0.8, 1)]
    jelly_spots = [(-2.5, 3.0, 3.0, 0.55), (2.8, 5.5, 3.9, 0.75), (0.3, 0.2, 3.6, 0.35),
                   (-5.0, 7.0, 4.6, 0.6), (4.5, 1.5, 2.7, 0.3)]
    for i, (x, y, z, s) in enumerate(jelly_spots):
        build_jellyfish(f"Jelly{i}", (x, y, z), s, jelly_colors[i % 3], life_c,
                        frame_end, phase=i * 13)

    build_plankton(life_c, frame_end)
    build_camera(cam_c, frame_end)
    setup_render(frame_end)
    bpy.context.scene.frame_set(OPTS["frame"])

    if OPTS["save"]:
        bpy.ops.wm.save_as_mainfile(filepath=bpy.path.abspath(OPTS["save"]))
        print("saved", OPTS["save"])
    if OPTS["render"]:
        bpy.context.scene.render.filepath = OPTS["render"]
        bpy.ops.render.render(write_still=True)
        print("rendered", OPTS["render"])


main()
