"""
flower_v008.blend 用リグ自動セットアップ

使い方:
  - Blender で flower_v008.blend を開き、Scripting タブでこのファイルを開いて「実行」
  - またはコマンドライン:
      blender -b flower_v008.blend --python rig_flower_fairy.py -- out.blend

作るもの:
  flower_rig : 根(固定) / 茎上部 FK / 花の頭 / 胃袋の鼓動(全体の脈動 + 6方向ローブ)
  fairy_rig  : 花の穴の根元 → 蛇のような柄(Spline IK) → 上半身(背骨・首・頭・腕 IK/FK)
               flower_rig の flower_head にぶら下がるので花と一緒に動く
"""

import bpy
import sys
import math
import bmesh
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

V = Vector


# ---------------------------------------------------------------- utils
def obj(name):
    return bpy.data.objects[name]


def set_active(o):
    for s in bpy.context.view_layer.objects:
        s.select_set(False)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)


def mode(m):
    bpy.ops.object.mode_set(mode=m)


def link_to(o, coll_name):
    coll = bpy.data.collections.get(coll_name)
    if coll is None:
        coll = bpy.data.collections.new(coll_name)
        bpy.context.scene.collection.children.link(coll)
    for c in list(o.users_collection):
        c.objects.unlink(o)
    coll.objects.link(o)
    return coll


def new_armature(name, coll):
    data = bpy.data.armatures.new(name)
    data.display_type = 'OCTAHEDRAL'
    o = bpy.data.objects.new(name, data)
    o.show_in_front = True
    link_to(o, coll)
    return o


def add_bone(arm, name, head, tail, parent=None, deform=True, roll_axis=None,
             connect=False):
    eb = arm.data.edit_bones.new(name)
    eb.head = V(head)
    eb.tail = V(tail)
    if roll_axis is not None:
        eb.align_roll(V(roll_axis))
    if parent:
        eb.parent = arm.data.edit_bones[parent]
        eb.use_connect = connect
    eb.use_deform = deform
    return eb


def bone_coll(arm, coll_name, bone_names):
    bc = arm.data.collections.get(coll_name) or arm.data.collections.new(coll_name)
    for n in bone_names:
        bc.assign(arm.data.bones[n])
    return bc


def widget(name, kind='circle', radius=1.0):
    """コントローラー用の形状 (WGT コレクションに隠して置く)"""
    wname = 'WGT_' + name
    if wname in bpy.data.objects:
        return bpy.data.objects[wname]
    me = bpy.data.meshes.new(wname)
    bm = bmesh.new()
    if kind == 'circle':
        bmesh.ops.create_circle(bm, segments=24, radius=radius)
    elif kind == 'cube':
        bmesh.ops.create_cube(bm, size=radius)
    elif kind == 'diamond':
        vs = [bm.verts.new(p) for p in [(radius, 0, 0), (0, radius, 0),
                                        (-radius, 0, 0), (0, -radius, 0),
                                        (0, 0, radius), (0, 0, -radius)]]
        for a, b in [(0, 1), (1, 2), (2, 3), (3, 0), (0, 4), (2, 4), (0, 5), (2, 5),
                     (1, 4), (3, 4), (1, 5), (3, 5)]:
            bm.edges.new((vs[a], vs[b]))
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(wname, me)
    coll = link_to(o, 'WGT')
    coll.hide_viewport = True
    coll.hide_render = True
    return o


def ensure_armature_mod(mesh_obj, arm, first=True):
    for m in list(mesh_obj.modifiers):
        if m.type == 'ARMATURE':
            mesh_obj.modifiers.remove(m)
    m = mesh_obj.modifiers.new('Armature', 'ARMATURE')
    m.object = arm
    if first:
        mesh_obj.modifiers.move(len(mesh_obj.modifiers) - 1, 0)
    return m


def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def parent_to_bone(child, arm, bone_name):
    mw = child.matrix_world.copy()
    child.parent = arm
    child.parent_type = 'BONE'
    child.parent_bone = bone_name
    bpy.context.view_layer.update()
    pb = arm.pose.bones[bone_name]
    # BONE 親はボーンの tail が原点
    parent_mat = arm.matrix_world @ pb.matrix @ Matrix.Translation(V((0, pb.length, 0)))
    child.matrix_parent_inverse = parent_mat.inverted()
    child.matrix_world = mw


# ================================================================ FLOWER
def build_flower(scene_coll):
    arm = new_armature('flower_rig', scene_coll)
    set_active(arm)
    mode('EDIT')

    add_bone(arm, 'flower_root', (0, 0, 0), (0, -2.5, 0), deform=True)

    # 茎上部 (胃袋より上、花の付け根まで) を FK チェーンに
    stalk_pts = [(-0.30, -0.20, 11.6), (-0.20, -0.22, 12.6), (-0.12, -0.15, 13.6),
                 (-0.06, 0.00, 14.6), (0.02, 0.06, 15.6), (0.03, 0.03, 16.6)]
    prev = 'flower_root'
    stalk_names = []
    for i in range(len(stalk_pts) - 1):
        n = f'stalk_{i + 1:02d}'
        add_bone(arm, n, stalk_pts[i], stalk_pts[i + 1], parent=prev,
                 connect=(i > 0), roll_axis=(0, -1, 0))
        stalk_names.append(n)
        prev = n
    add_bone(arm, 'flower_head', stalk_pts[-1], (0.03, 0.03, 18.4), parent=prev,
             connect=True, roll_axis=(0, -1, 0))

    # 胃袋: 中心から全体脈動 + 6 方向ローブ
    st = obj('stomach_geo')
    bb = [st.matrix_world @ V(c) for c in st.bound_box]
    c = (V((min(p.x for p in bb), min(p.y for p in bb), min(p.z for p in bb))) +
         V((max(p.x for p in bb), max(p.y for p in bb), max(p.z for p in bb)))) / 2
    add_bone(arm, 'stomach_pulse', c, c + V((0, 0, 1.2)), parent='flower_root',
             deform=False, roll_axis=(0, -1, 0))
    lobe_dirs = {'stomach_lobe_px': (1, 0, 0), 'stomach_lobe_nx': (-1, 0, 0),
                 'stomach_lobe_py': (0, 1, 0), 'stomach_lobe_ny': (0, -1, 0),
                 'stomach_lobe_pz': (0, 0, 1), 'stomach_lobe_nz': (0, 0, -1)}
    for n, d in lobe_dirs.items():
        roll = (0, 0, 1) if abs(d[2]) < 0.5 else (0, -1, 0)
        add_bone(arm, n, c, c + V(d) * 1.5, parent='stomach_pulse', roll_axis=roll)
    # 操作用コントローラー (胃袋の正面に置く。プロパティはここ)
    front = V((c.x, c.y - 2.8, c.z))
    add_bone(arm, 'stomach_ctrl', front, front + V((0, 0, 0.8)), parent='flower_root',
             deform=False)

    mode('POSE')
    pb = arm.pose.bones
    # 胃袋の鼓動プロパティ
    ctrl = pb['stomach_ctrl']
    props = {'auto_beat': (1.0, 0.0, 1.0, '1=自動で鼓動 / 0=手動 (beat をキー打ち)'),
             'bpm': (70.0, 1.0, 300.0, '1分あたりの鼓動回数'),
             'amount': (0.12, 0.0, 1.0, '膨らみの強さ'),
             'wobble': (0.6, 0.0, 2.0, 'ローブごとのずれ(バクバク感)'),
             'beat': (0.0, -1.0, 2.0, '手動の膨らみ (キーで打つ)')}
    for k, (val, lo, hi, desc) in props.items():
        ctrl[k] = val
        ui = ctrl.id_properties_ui(k)
        ui.update(min=lo, max=hi, soft_min=lo, soft_max=hi, description=desc)
    for n in ['stomach_ctrl']:
        pb[n].lock_location = (False, False, False)

    def heart_expr(phase):
        # lub-dub 形の鼓動 (simple expression のみで評価できる式)
        u = f"fmod(frame*bpm/(60*fps)+{phase}+100.5,1)"
        return (f"(exp(-pow(({u}-0.10)/0.05,2))"
                f"+0.6*exp(-pow(({u}-0.32)/0.06,2)))")

    def add_driver(pbone, path, idx, expr_body):
        fc = pbone.driver_add(path, idx)
        drv = fc.driver
        drv.type = 'SCRIPTED'
        for vname in ['auto_beat', 'bpm', 'amount', 'wobble', 'beat']:
            v = drv.variables.new()
            v.name = vname
            v.type = 'SINGLE_PROP'
            v.targets[0].id_type = 'OBJECT'
            v.targets[0].id = arm
            v.targets[0].data_path = f'pose.bones["stomach_ctrl"]["{vname}"]'
        v = drv.variables.new()
        v.name = 'fps'
        v.type = 'SINGLE_PROP'
        v.targets[0].id_type = 'SCENE'
        v.targets[0].id = bpy.context.scene
        v.targets[0].data_path = 'render.fps'
        drv.expression = expr_body
        return drv

    pulse = pb['stomach_pulse']
    for i in range(3):
        add_driver(pulse, 'scale', i,
                   f"1+amount*(auto_beat*{heart_expr(0)}+beat)")
    phases = [0.03, 0.09, 0.05, 0.12, 0.0, 0.07]
    gains = [1.0, 0.8, 1.2, 0.9, 0.7, 1.1]
    for (n, ph, g) in zip(lobe_dirs.keys(), phases, gains):
        add_driver(pb[n], 'scale', 1,
                   f"1+amount*wobble*{g}*(auto_beat*{heart_expr(ph)}+beat)")

    # 形状
    pb['flower_root'].custom_shape = widget('flower_root', 'circle', 3.0)
    pb['flower_root'].custom_shape_rotation_euler = (math.pi / 2, 0, 0)
    for n in stalk_names:
        pb[n].custom_shape = widget('stalk', 'circle', 0.5)
    pb['flower_head'].custom_shape = widget('flower_head', 'circle', 1.4)
    pb['flower_head'].custom_shape_scale_xyz = (1, 1, 1)
    pb['stomach_ctrl'].custom_shape = widget('stomach_ctrl', 'diamond', 0.5)
    pb['stomach_pulse'].custom_shape = widget('stomach_pulse', 'circle', 1.0)
    for n in lobe_dirs:
        pb[n].custom_shape = widget('lobe', 'diamond', 0.15)
    mode('OBJECT')

    bone_coll(arm, 'Controls', ['flower_root', 'flower_head', 'stomach_ctrl'] + stalk_names)
    bone_coll(arm, 'Stomach (driven)', ['stomach_pulse'] + list(lobe_dirs))
    arm.data.collections['Stomach (driven)'].is_visible = False

    weight_flower(arm, stalk_pts, stalk_names, c, lobe_dirs)
    return arm


def weight_flower(arm, stalk_pts, stalk_names, center, lobe_dirs):
    joints = [p[2] for p in stalk_pts]          # 11.6 ... 16.6
    chain = ['flower_root'] + stalk_names + ['flower_head']
    blend = 0.35

    def chain_weights(z):
        """z 方向に沿ったチェーンのウェイト (継ぎ目でなめらかにブレンド)"""
        w = {}
        for j, zj in enumerate(joints):
            lower, upper = chain[j], chain[j + 1]
            t = smoothstep(zj - blend, zj + blend, z)
            if z < zj - blend:
                w = {lower: 1.0}
                return w
            if z <= zj + blend:
                return {lower: 1 - t, upper: t}
        return {chain[-1]: 1.0}

    # 胃袋の表面 BVH (蔓を胃袋に追従させる)
    st = obj('stomach_geo')
    st_world = [st.matrix_world @ v.co for v in st.data.vertices]
    bvh = BVHTree.FromPolygons(st_world, [p.vertices[:] for p in st.data.polygons])
    dirs = {n: V(d) for n, d in lobe_dirs.items()}

    def lobe_weights(p):
        d = p - center
        if d.length < 1e-6:
            return {n: 1 / 6 for n in dirs}
        d.normalize()
        w = {n: max(0.0, d.dot(a)) ** 2 for n, a in dirs.items()}
        s = sum(w.values()) or 1
        return {n: x / s for n, x in w.items() if x > 1e-4}

    def assign(o, weights_per_vert):
        o.vertex_groups.clear()
        groups = {}
        for i, wd in enumerate(weights_per_vert):
            for bn, w in wd.items():
                if w <= 1e-4:
                    continue
                g = groups.get(bn) or o.vertex_groups.new(name=bn)
                groups[bn] = g
                g.add([i], w, 'REPLACE')

    # 胃袋
    assign(st, [lobe_weights(p) for p in st_world])
    ensure_armature_mod(st, arm)

    # 茎 (根は固定、上部はチェーン、胃袋に絡む部分は胃袋に追従)
    near_in, near_out = 0.08, 0.45
    for n in ['stem_geo1', 'stem_geo2', 'stem_geo3', 'stem_geo4', 'stem_geo5']:
        o = obj(n)
        ws = []
        for v in o.data.vertices:
            p = o.matrix_world @ v.co
            base = chain_weights(p.z)
            hit = bvh.find_nearest(p)
            dist = (hit[0] - p).length if hit[0] is not None else 1e9
            # 胃袋の内側にある点も追従
            inside = hit[0] is not None and hit[1].dot(p - hit[0]) < 0
            k = 1.0 if inside else 1.0 - smoothstep(near_in, near_out, dist)
            if k > 0:
                lw = lobe_weights(p)
                wd = {b: x * (1 - k) for b, x in base.items()}
                for b, x in lw.items():
                    wd[b] = wd.get(b, 0) + x * k
            else:
                wd = base
            ws.append(wd)
        assign(o, ws)
        ensure_armature_mod(o, arm)

    # 花びら・口(喉) は花の頭に 100%
    for n in ['petal_geo', 'mouth_geo']:
        o = obj(n)
        assign(o, [{'flower_head': 1.0}] * len(o.data.vertices))
        ensure_armature_mod(o, arm)


# ================================================================ FAIRY
def build_fairy(scene_coll, flower_arm):
    arm = new_armature('fairy_rig', scene_coll)
    arm.data.display_type = 'BBONE'
    set_active(arm)
    mode('EDIT')

    Y = 0.03                    # 体の中心 (y)
    z_root, z_hip = 16.25, 20.0
    add_bone(arm, 'fairy_root', (0, Y, z_root), (0, Y - 0.25, z_root), deform=False)

    # --- 柄 (蛇の部分): 16 本 + Spline IK
    n_seg = 16
    stalk = []
    prev = None
    for i in range(n_seg):
        z0 = z_root + (z_hip - z_root) * i / n_seg
        z1 = z_root + (z_hip - z_root) * (i + 1) / n_seg
        n = f'tail_{i + 1:02d}'
        add_bone(arm, n, (0, Y, z0), (0, Y, z1), parent=prev or 'fairy_root',
                 connect=prev is not None, roll_axis=(0, -1, 0))
        stalk.append(n)
        prev = n

    # 柄のコントローラー (カーブの制御点をフック)
    n_ctrl = 5
    ctrl_names = []
    for i in range(n_ctrl):
        z = z_root + (z_hip - z_root) * i / (n_ctrl - 1)
        n = 'tail_ctrl_base' if i == 0 else ('body_ctrl' if i == n_ctrl - 1
                                              else f'tail_ctrl_{i:02d}')
        add_bone(arm, n, (0, Y, z), (0, Y, z + 0.18), parent='fairy_root',
                 deform=False, roll_axis=(0, -1, 0))
        ctrl_names.append(n)

    # --- 上半身
    add_bone(arm, 'hips', (0, Y, 20.0), (0, Y, 20.20), parent='body_ctrl',
             roll_axis=(0, -1, 0))
    add_bone(arm, 'spine', (0, Y, 20.20), (0, Y, 20.36), parent='hips',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'chest', (0, Y, 20.36), (0, Y + 0.01, 20.58), parent='spine',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'neck', (0, Y + 0.01, 20.58), (0, 0.0, 20.665), parent='chest',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'head', (0, 0.0, 20.665), (0, 0.0, 20.95), parent='neck',
             connect=True, roll_axis=(0, -1, 0))

    for s, sx in (('L', 1), ('R', -1)):
        def P(x, y, z):
            return (x * sx, y, z)
        add_bone(arm, f'clavicle.{s}', P(0.02, 0.04, 20.555), P(0.11, 0.06, 20.54),
                 parent='chest', roll_axis=(0, 0, 1))
        add_bone(arm, f'upper_arm.{s}', P(0.11, 0.06, 20.54), P(0.30, 0.066, 20.448),
                 parent=f'clavicle.{s}', connect=True, roll_axis=(0, 0, 1))
        add_bone(arm, f'forearm.{s}', P(0.30, 0.066, 20.448), P(0.462, 0.0, 20.355),
                 parent=f'upper_arm.{s}', connect=True, roll_axis=(0, 0, 1))
        add_bone(arm, f'hand.{s}', P(0.462, 0.0, 20.355), P(0.585, -0.035, 20.268),
                 parent=f'forearm.{s}', connect=True, roll_axis=(0, 0, 1))
        # IK
        add_bone(arm, f'hand_ik.{s}', P(0.462, 0.0, 20.355), P(0.585, -0.035, 20.268),
                 parent='fairy_root', deform=False, roll_axis=(0, 0, 1))
        add_bone(arm, f'arm_pole.{s}', P(0.30, 0.30, 20.448), P(0.30, 0.36, 20.448),
                 parent='fairy_root', deform=False)

    mode('POSE')
    pb = arm.pose.bones

    # Spline IK 用カーブ
    cu = bpy.data.curves.new('fairy_tail_curve', 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(n_ctrl - 1)
    seg = (z_hip - z_root) / (n_ctrl - 1)
    for i, bp in enumerate(sp.bezier_points):
        z = z_root + seg * i
        bp.co = V((0, Y, z))
        bp.handle_left = V((0, Y, z - seg / 3))
        bp.handle_right = V((0, Y, z + seg / 3))
        bp.handle_left_type = bp.handle_right_type = 'ALIGNED'
    cobj = bpy.data.objects.new('fairy_tail_curve', cu)
    link_to(cobj, scene_coll)
    cobj.parent = arm
    cobj.hide_render = True
    bpy.context.view_layer.update()
    for i, n in enumerate(ctrl_names):
        h = cobj.modifiers.new(f'Hook_{n}', 'HOOK')
        h.object = arm
        h.subtarget = n
        h.vertex_indices_set([i * 3, i * 3 + 1, i * 3 + 2])
        bone_world = arm.matrix_world @ pb[n].matrix
        h.matrix_inverse = (cobj.matrix_world.inverted() @ bone_world).inverted()
        h.center = cobj.matrix_world.inverted() @ bone_world.translation

    ik = pb[stalk[-1]].constraints.new('SPLINE_IK')
    ik.target = cobj
    ik.chain_count = n_seg
    ik.use_curve_radius = False
    ik.y_scale_mode = 'FIT_CURVE'
    ik.xz_scale_mode = 'NONE'
    ik.use_even_divisions = True

    # 柄の根元の太さを保つための体積維持はなし (蛇なので伸縮は FIT_CURVE に任せる)

    # 腕 IK/FK
    for s in ('L', 'R'):
        ctrl = pb[f'hand_ik.{s}']
        ctrl['ik_fk'] = 0.0
        ctrl.id_properties_ui('ik_fk').update(min=0.0, max=1.0,
                                              description='0=FK / 1=IK')
        c = pb[f'forearm.{s}'].constraints.new('IK')
        c.target = arm
        c.subtarget = f'hand_ik.{s}'
        c.pole_target = arm
        c.pole_subtarget = f'arm_pole.{s}'
        c.pole_angle = math.radians(-90)
        c.chain_count = 2
        cr = pb[f'hand.{s}'].constraints.new('COPY_ROTATION')
        cr.target = arm
        cr.subtarget = f'hand_ik.{s}'
        for con in (c, cr):
            fc = con.driver_add('influence')
            d = fc.driver
            d.type = 'AVERAGE'
            v = d.variables.new()
            v.type = 'SINGLE_PROP'
            v.targets[0].id = arm
            v.targets[0].data_path = f'pose.bones["hand_ik.{s}"]["ik_fk"]'

    # 形状
    sz = 0.12
    pb['fairy_root'].custom_shape = widget('fairy_root', 'circle', 0.25)
    pb['fairy_root'].custom_shape_rotation_euler = (math.pi / 2, 0, 0)
    for n in ctrl_names:
        pb[n].custom_shape = widget('tail_ctrl', 'circle', 0.5)
        pb[n].custom_shape_scale_xyz = (sz, sz, sz)
        pb[n].custom_shape_rotation_euler = (math.pi / 2, 0, 0)
    pb['body_ctrl'].custom_shape_scale_xyz = (0.25, 0.25, 0.25)
    for n in ['hips', 'spine', 'chest', 'neck', 'head']:
        pb[n].custom_shape = widget('fk', 'circle', 0.5)
        pb[n].custom_shape_scale_xyz = (0.25, 0.25, 0.25)
    for s in ('L', 'R'):
        for n in (f'clavicle.{s}', f'upper_arm.{s}', f'forearm.{s}', f'hand.{s}'):
            pb[n].custom_shape = widget('fk', 'circle', 0.5)
            pb[n].custom_shape_scale_xyz = (0.12, 0.12, 0.12)
        pb[f'hand_ik.{s}'].custom_shape = widget('ik', 'cube', 1.0)
        pb[f'hand_ik.{s}'].custom_shape_scale_xyz = (0.3, 0.3, 0.3)
        pb[f'arm_pole.{s}'].custom_shape = widget('pole', 'diamond', 0.5)
        pb[f'arm_pole.{s}'].custom_shape_scale_xyz = (0.08, 0.08, 0.08)
    mode('OBJECT')

    bone_coll(arm, 'Tail Controls', ['fairy_root'] + ctrl_names)
    bone_coll(arm, 'Body FK', ['hips', 'spine', 'chest', 'neck', 'head'] +
              [f'{b}.{s}' for s in 'LR' for b in ('clavicle', 'upper_arm', 'forearm', 'hand')])
    bone_coll(arm, 'Arm IK', [f'{b}.{s}' for s in 'LR' for b in ('hand_ik', 'arm_pole')])
    bone_coll(arm, 'Tail Deform', stalk)
    arm.data.collections['Tail Deform'].is_visible = False

    # 花の頭にぶら下げる
    parent_to_bone(arm, flower_arm, 'flower_head')

    # ウェイト (自動ウェイト = ボーンヒート)
    fairy = obj('fairy_geo')
    fairy.vertex_groups.clear()
    set_active(arm)
    fairy.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    # parent_set は親子付けもするので外して Armature モディファイアだけ残す
    mw = fairy.matrix_world.copy()
    fairy.parent = None
    fairy.matrix_world = mw
    ensure_armature_mod(fairy, arm)
    fix_fairy_weights(fairy, arm, stalk)

    # 目は頭にくっつける
    for n in ['eye_L_geo', 'eye_R_geo']:
        parent_to_bone(obj(n), arm, 'head')
    return arm


def fix_fairy_weights(fairy, arm, stalk):
    """ボーンヒートが取りこぼした頂点・柄部分を補正"""
    deform = [b for b in arm.data.bones if b.use_deform]
    segs = {b.name: (arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local)
            for b in deform}
    gidx = {g.index: g.name for g in fairy.vertex_groups}

    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
        return (a + ab * t - p).length

    empty = 0
    for v in fairy.data.vertices:
        p = fairy.matrix_world @ v.co
        total = sum(g.weight for g in v.groups)
        # 柄 (腰より下) は z に沿ったきれいなウェイトに置き換える
        if p.z < 19.95:
            for g in list(v.groups):
                fairy.vertex_groups[g.group].remove([v.index])
            z_root, z_hip = 16.25, 20.0
            f = (p.z - z_root) / (z_hip - z_root) * len(stalk)
            f = min(max(f, 0.0), len(stalk) - 1e-4)
            i = int(f)
            t = f - i
            # 隣のボーンと 50% 幅でブレンド
            if t < 0.5 and i > 0:
                k = smoothstep(0.0, 0.5, t) * 0.5 + 0.5
                pairs = {stalk[i]: k, stalk[i - 1]: 1 - k}
            elif t >= 0.5 and i < len(stalk) - 1:
                k = smoothstep(0.5, 1.0, t) * 0.5
                pairs = {stalk[i]: 1 - k, stalk[i + 1]: k}
            else:
                pairs = {stalk[i]: 1.0}
            for bn, w in pairs.items():
                fairy.vertex_groups[bn].add([v.index], w, 'REPLACE')
            continue
        if total < 1e-3:
            empty += 1
            best = min(segs, key=lambda n: seg_dist(p, *segs[n]))
            g = fairy.vertex_groups.get(best) or fairy.vertex_groups.new(name=best)
            g.add([v.index], 1.0, 'REPLACE')
    # 腰〜柄の継ぎ目: 腰のウェイトに最上段の柄をなじませる
    print(f'[rig] fairy: filled {empty} unweighted verts')


# ================================================================ main
def main(out_path=None):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        mode('OBJECT')
    # 元ファイルの未使用 Armature (6 本の仮ボーン、どのメッシュにも未バインド)
    old = bpy.data.objects.get('Armature')
    if old and old.type == 'ARMATURE' and not any(
            m.type == 'ARMATURE' and m.object == old
            for o in bpy.data.objects for m in getattr(o, 'modifiers', [])):
        bpy.data.objects.remove(old)
    for n in ['flower_rig', 'fairy_rig', 'fairy_tail_curve']:
        if n in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[n])

    coll = 'flower_fairy_grp'
    flower = build_flower(coll)
    fairy = build_fairy(coll, flower)
    for o in (flower, fairy):
        o.data.pose_position = 'POSE'
    set_active(flower)
    bpy.context.scene.frame_set(1)
    if out_path:
        bpy.ops.wm.save_as_mainfile(filepath=out_path)
        print('[rig] saved', out_path)


if __name__ == '__main__':
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if argv and argv[0].endswith('.blend') and len(argv) >= 2:
        bpy.ops.wm.open_mainfile(filepath=argv[0])
        main(argv[1])
    elif argv:
        main(argv[0])
    else:
        main()
