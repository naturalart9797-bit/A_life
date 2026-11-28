"""
flower_v008.blend 用 妖精リグ + 胃袋の膨張・収縮 セットアップ

使い方:
  Blender で flower_v008.blend を開き、Scripting タブでこのファイルを開いて「実行」
  (またはコマンドライン: blender -b flower_v008.blend --python rig_flower_fairy.py -- out.blend)

方針: リグ以外は変えない
  - 花・茎・花びら・口・カメラ・シーン設定・既存の Armature には触らない
  - 変更するのは fairy_geo (ウェイト + Armature モディファイア) と目 (head にペアレント) だけ
  - 胃袋はボーンを入れず、シェイプキー「膨張」「収縮」で動かす
    (胃袋に絡む蔓も同じスライダーに追従。値 0 なら元と完全に同じ形)

fairy_rig:
  root (口の底の穴)
   ├ tail_base / tail_ctrl.01〜12 / torso : 蛇部分のコントローラー (Spline IK, 骨 40 本, 伸び縮みなし)
   └ DEF-tail.040 (蛇の先端) → hips → spine → spine.001 → chest → neck → head
                                         chest → shoulder → 腕 (IK/FK 切替) → 指
"""

import bpy
import sys
import math
import bmesh
from mathutils import Vector, Matrix
from mathutils.bvhtree import BVHTree

V = Vector

Y = 0.03            # 体の中心 (y)
HOLE_Z = 16.98      # 口 (mouth_geo) の底の穴
HIP_Z = 20.0
N_TAIL = 40         # 蛇部分の骨の数
N_TAIL_CTRL = 12    # 途中のコントローラーの数 (根元と torso を除く)
RIG_COLL = 'fairy_grp'
WGT_COLL = 'fairy_rig_widgets'

# 左手の指 (付け根 xy, 指先 xyz)。右手は x を反転
FINGERS = {
    'thumb':  ((0.497, -0.040), (0.509, -0.073, 20.280)),
    'index':  ((0.534, -0.046), (0.5747, -0.0628, 20.2748)),
    'middle': ((0.536, -0.031), (0.5801, -0.0398, 20.2687)),
    'ring':   ((0.535, -0.017), (0.5789, -0.0231, 20.2706)),
    'pinky':  ((0.532, 0.000), (0.5693, 0.0012, 20.2816)),
}

COL_CENTER, COL_L, COL_R, COL_IK, COL_TAIL = 'THEME09', 'THEME04', 'THEME01', 'THEME02', 'THEME03'


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


def get_coll(name, parent=None, exclude=False):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    if exclude:
        def find(lc):
            if lc.collection == coll:
                return lc
            for c in lc.children:
                r = find(c)
                if r:
                    return r
        lc = find(bpy.context.view_layer.layer_collection)
        if lc:
            lc.exclude = True
    return coll


def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


# ---------------------------------------------------------------- widgets
def _wire(name, verts, edges):
    wname = 'WGT-' + name
    if wname in bpy.data.objects:
        return bpy.data.objects[wname]
    me = bpy.data.meshes.new(wname)
    me.from_pydata(verts, edges, [])
    o = bpy.data.objects.new(wname, me)
    o.hide_render = True
    get_coll(WGT_COLL).objects.link(o)
    return o


def wgt_circle(name, r, seg=32):
    """ボーンの軸 (Y) に垂直な円"""
    vs = [(r * math.cos(2 * math.pi * i / seg), 0, r * math.sin(2 * math.pi * i / seg))
          for i in range(seg)]
    return _wire(name, vs, [(i, (i + 1) % seg) for i in range(seg)])


def wgt_box(name, sx, sy, sz, y0=0.0):
    vs = [(x * sx, y0 + y * sy, z * sz) for x in (-1, 1) for y in (0, 1) for z in (-1, 1)]
    es = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
          (0, 4), (1, 5), (2, 6), (3, 7)]
    return _wire(name, vs, es)


def wgt_sphere(name, r, seg=16):
    vs, es = [], []
    for axis in range(3):
        base = len(vs)
        for i in range(seg):
            a = 2 * math.pi * i / seg
            p = [0, 0, 0]
            p[(axis + 1) % 3] = r * math.cos(a)
            p[(axis + 2) % 3] = r * math.sin(a)
            vs.append(tuple(p))
            es.append((base + i, base + (i + 1) % seg))
    return _wire(name, vs, es)


# ---------------------------------------------------------------- bones
class Rig:
    def __init__(self, name, location):
        data = bpy.data.armatures.new(name)
        data.display_type = 'OCTAHEDRAL'
        self.o = bpy.data.objects.new(name, data)
        self.o.location = V(location)
        self.o.show_in_front = True
        get_coll(RIG_COLL).objects.link(self.o)
        bpy.context.view_layer.update()
        self.inv = self.o.matrix_world.inverted()
        self.colls = {}

    def bone(self, name, head, tail, parent=None, deform=False, roll=(0, -1, 0),
             connect=False, coll=None):
        eb = self.o.data.edit_bones.new(name)
        eb.head = self.inv @ V(head)
        eb.tail = self.inv @ V(tail)
        if roll is not None:
            eb.align_roll(self.inv.to_3x3() @ V(roll))
        if parent:
            eb.parent = self.o.data.edit_bones[parent]
            eb.use_connect = connect
        eb.use_deform = deform
        if coll:
            self.colls.setdefault(coll, []).append(name)
        return eb

    def finish_collections(self, hidden=()):
        for cname, names in self.colls.items():
            bc = self.o.data.collections.new(cname)
            for n in names:
                bc.assign(self.o.data.bones[n])
            bc.is_visible = cname not in hidden

    def shape(self, name, wgt, color, rot=None):
        pb = self.o.pose.bones[name]
        pb.custom_shape = wgt
        pb.use_custom_shape_bone_size = False
        if rot:
            pb.custom_shape_rotation_euler = rot
        pb.color.palette = color
        self.o.data.bones[name].color.palette = color


# ================================================================ FAIRY
def mesh_center_near(verts, x, y, r=0.005, z_hint=None):
    s = [p for p in verts if (p.x - x) ** 2 + (p.y - y) ** 2 < r * r and
         (z_hint is None or abs(p.z - z_hint) < 0.04)]
    return sum(p.z for p in s) / len(s) if s else None


def build_fairy():
    fairy = obj('fairy_geo')
    fverts = [fairy.matrix_world @ v.co for v in fairy.data.vertices]
    rig = Rig('fairy_rig', (0, Y, HOLE_Z))
    arm = rig.o
    set_active(arm)
    mode('EDIT')

    # --- root と蛇部分
    rig.bone('root', (0, Y, HOLE_Z), (0, Y, HOLE_Z + 0.25), coll='Root')
    rig.bone('DEF-tail_base', (0, Y, HOLE_Z), (0, Y, HOLE_Z - 0.3), parent='root',
             deform=True, coll='Deform')
    tail = []
    prev = 'root'
    for i in range(N_TAIL):
        z0 = HOLE_Z + (HIP_Z - HOLE_Z) * i / N_TAIL
        z1 = HOLE_Z + (HIP_Z - HOLE_Z) * (i + 1) / N_TAIL
        n = f'DEF-tail.{i + 1:03d}'
        rig.bone(n, (0, Y, z0), (0, Y, z1), parent=prev, connect=i > 0,
                 deform=True, coll='Deform')
        tail.append(n)
        prev = n

    n_pts = N_TAIL_CTRL + 2
    ctrl_pts = []
    for i in range(n_pts):
        z = HOLE_Z + (HIP_Z - HOLE_Z) * i / (n_pts - 1)
        if i == 0:
            n = 'tail_base'
        elif i == n_pts - 1:
            n = 'torso'
        else:
            n = f'tail_ctrl.{i:02d}'
        ctrl_pts.append((n, z))
        L = 0.12
        rig.bone(n, (0, Y, z), (0, Y, z + L), parent='root',
                 coll='Torso' if n == 'torso' else 'Tail')

    # --- 体幹 (deform ボーンをそのまま FK コントローラーとして使う)
    # hips は蛇部分の先端の骨にぶら下げる (伸び縮みしないので体が常に先端に乗る)
    body = [('hips', (0, Y, 20.0), (0, Y, 20.20), f'DEF-tail.{N_TAIL:03d}'),
            ('spine', (0, Y, 20.20), (0, Y, 20.30), 'hips'),
            ('spine.001', (0, Y, 20.30), (0, Y, 20.40), 'spine'),
            ('chest', (0, Y, 20.40), (0, Y + 0.01, 20.58), 'spine.001'),
            ('neck', (0, Y + 0.01, 20.58), (0, 0.0, 20.665), 'chest'),
            ('head', (0, 0.0, 20.665), (0, 0.0, 20.95), 'neck')]
    for n, h, t, p in body:
        rig.bone(n, h, t, parent=p, deform=True, connect=True,
                 coll='Torso' if n == 'hips' else 'Body')

    # --- 腕・手・指
    arm_pts = {}
    for s, sx in (('L', 1), ('R', -1)):
        def P(x, y, z):
            return V((x * sx, y, z))
        sh, el, wr = P(0.11, 0.06, 20.54), P(0.30, 0.066, 20.448), P(0.462, 0.0, 20.355)
        mid = FINGERS['middle'][0]
        hand_tip = P(mid[0], mid[1], mesh_center_near(fverts, mid[0], mid[1], z_hint=20.31) or 20.31)
        arm_pts[s] = (sh, el, wr, hand_tip)
        rig.bone(f'shoulder.{s}', P(0.02, 0.04, 20.555), sh, parent='chest', deform=True,
                 roll=(0, 0, 1), coll='Arms')
        rig.bone(f'upper_arm.{s}', sh, el, parent=f'shoulder.{s}', connect=True,
                 deform=True, roll=(0, 0, 1), coll='Arms')
        rig.bone(f'forearm.{s}', el, wr, parent=f'upper_arm.{s}', connect=True,
                 deform=True, roll=(0, 0, 1), coll='Arms')
        rig.bone(f'hand.{s}', wr, hand_tip, parent=f'forearm.{s}', connect=True,
                 deform=True, roll=(0, 0, 1), coll='Arms')
        # IK
        rig.bone(f'hand_ik.{s}', wr, hand_tip, parent='hips', roll=(0, 0, 1),
                 coll='Arm IK')
        pole = el + V((0, 0.25, 0))
        rig.bone(f'arm_pole.{s}', pole, pole + V((0, 0.05, 0)), parent='hips',
                 roll=(0, 0, 1), coll='Arm IK')
        for f, ((bx, by), tip) in FINGERS.items():
            base = V((bx, by, mesh_center_near(fverts, bx, by, z_hint=tip[2]) or tip[2]))
            tip = V(tip)
            pts = [base]
            for t in (0.42, 0.75):
                p = base.lerp(tip, t)
                z = mesh_center_near(fverts, p.x, p.y, r=0.004, z_hint=p.z)
                pts.append(V((p.x, p.y, z if z is not None else p.z)))
            pts.append(tip)
            prevf = f'hand.{s}'
            for k in range(3):
                n = f'{f}.{k + 1:02d}.{s}'
                a, b = pts[k], pts[k + 1]
                rig.bone(n, P(a.x, a.y, a.z), P(b.x, b.y, b.z), parent=prevf,
                         connect=k > 0, deform=True, roll=(0, 0, 1), coll='Fingers')
                prevf = n
    mode('OBJECT')
    rig.finish_collections(hidden=('Deform',))

    # ---------------- 蛇部分: Spline IK カーブ
    cu = bpy.data.curves.new('fairy_tail_curve', 'CURVE')
    cu.dimensions = '3D'
    sp = cu.splines.new('BEZIER')
    sp.bezier_points.add(n_pts - 1)
    seg = (HIP_Z - HOLE_Z) / (n_pts - 1)
    for i, bp in enumerate(sp.bezier_points):
        z = ctrl_pts[i][1]
        bp.co = V((0, Y, z))
        bp.handle_left = V((0, Y, z - seg / 3))
        bp.handle_right = V((0, Y, z + seg / 3))
        bp.handle_left_type = bp.handle_right_type = 'ALIGNED'
    cobj = bpy.data.objects.new('fairy_tail_curve', cu)
    cobj.hide_render = True
    cobj.hide_select = True
    get_coll(RIG_COLL).objects.link(cobj)
    cobj.parent = arm
    cobj.matrix_parent_inverse = arm.matrix_world.inverted()
    bpy.context.view_layer.update()
    pb = arm.pose.bones
    for i, (n, _) in enumerate(ctrl_pts):
        h = cobj.modifiers.new(f'Hook-{n}', 'HOOK')
        h.object = arm
        h.subtarget = n
        h.vertex_indices_set([i * 3, i * 3 + 1, i * 3 + 2])
        bw = arm.matrix_world @ pb[n].matrix
        h.matrix_inverse = (cobj.matrix_world.inverted() @ bw).inverted()
        h.center = cobj.matrix_world.inverted() @ bw.translation
    ik = pb[tail[-1]].constraints.new('SPLINE_IK')
    ik.target = cobj
    ik.chain_count = N_TAIL
    ik.y_scale_mode = 'BONE_ORIGINAL'     # 伸び縮みしない
    ik.xz_scale_mode = 'NONE'
    ik.use_curve_radius = False
    ik.use_even_divisions = True

    # ---------------- 腕 IK / FK
    for s in ('L', 'R'):
        ctrl = pb[f'hand_ik.{s}']
        ctrl['IK_FK'] = 1.0
        ctrl.id_properties_ui('IK_FK').update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0,
                                              description='1 = IK (hand_ik で動かす) / 0 = FK (腕の骨を回す)')
        c = pb[f'forearm.{s}'].constraints.new('IK')
        c.name = 'IK'
        c.target, c.subtarget = arm, f'hand_ik.{s}'
        c.pole_target, c.pole_subtarget = arm, f'arm_pole.{s}'
        c.chain_count = 2
        c.pole_angle = best_pole_angle(arm, s)
        cr = pb[f'hand.{s}'].constraints.new('COPY_TRANSFORMS')
        cr.name = 'IK hand'
        cr.target, cr.subtarget = arm, f'hand_ik.{s}'
        cr.mix_mode = 'REPLACE'
        for con in (c, cr):
            d = con.driver_add('influence').driver
            d.type = 'AVERAGE'
            v = d.variables.new()
            v.type = 'SINGLE_PROP'
            v.targets[0].id = arm
            v.targets[0].data_path = f'pose.bones["hand_ik.{s}"]["IK_FK"]'

    # ---------------- 形状と色
    rig.shape('root', wgt_circle('root', 0.30), COL_CENTER)
    for n, z in ctrl_pts:
        if n == 'torso':
            rig.shape(n, wgt_circle('torso', 0.20), COL_CENTER)
        elif n == 'tail_base':
            rig.shape(n, wgt_circle('tail_base', 0.16), COL_TAIL)
        else:
            rig.shape(n, wgt_circle('tail_ctrl', 0.13), COL_TAIL)
    sizes = {'hips': 0.13, 'spine': 0.12, 'spine.001': 0.12, 'chest': 0.15,
             'neck': 0.06, 'head': 0.13}
    for n, r in sizes.items():
        rig.shape(n, wgt_circle(n, r), COL_CENTER)
    for s, col in (('L', COL_L), ('R', COL_R)):
        rig.shape(f'shoulder.{s}', wgt_circle('shoulder', 0.05), col)
        rig.shape(f'upper_arm.{s}', wgt_circle('upper_arm', 0.04), col)
        rig.shape(f'forearm.{s}', wgt_circle('forearm', 0.035), col)
        rig.shape(f'hand.{s}', wgt_circle('hand', 0.04), col)
        rig.shape(f'hand_ik.{s}', wgt_box('hand_ik', 0.035, 0.11, 0.03, -0.015), COL_IK)
        rig.shape(f'arm_pole.{s}', wgt_sphere('arm_pole', 0.025), COL_IK)
        for f in FINGERS:
            for k in (1, 2, 3):
                rig.shape(f'{f}.{k:02d}.{s}', wgt_circle('finger', 0.008), col)
    # 動かさなくていいチャンネルはロック
    for n, _ in ctrl_pts:
        pb[n].rotation_mode = 'XYZ'
    for p in pb:
        if p.name.startswith(('upper_arm', 'forearm', 'hand', 'shoulder', 'neck', 'head',
                              'spine', 'chest', 'hips', 'thumb', 'index', 'middle', 'ring',
                              'pinky')) and '_ik' not in p.name:
            p.lock_location = (True, True, True)
    for s in 'LR':
        pb[f'arm_pole.{s}'].lock_rotation = (True, True, True)

    # ---------------- ウェイト
    weight_fairy(fairy, arm, tail)
    for n in ['eye_L_geo', 'eye_R_geo']:
        parent_to_bone(obj(n), arm, 'head')
    return arm


def best_pole_angle(arm, s):
    """レストポーズで腕がねじれない pole_angle を探す"""
    bones = arm.data.bones
    base, ikb, pole = bones[f'upper_arm.{s}'], bones[f'forearm.{s}'], bones[f'arm_pole.{s}']
    axis = base.tail_local - base.head_local
    pole_normal = (ikb.tail_local - base.head_local).cross(pole.head_local - base.head_local)
    proj = pole_normal.cross(axis)
    x_axis = base.matrix_local.col[0].to_3d()
    ang = x_axis.angle(proj)
    if x_axis.cross(proj).angle(axis) < 1:
        ang = -ang
    return ang


def weight_fairy(fairy, arm, tail):
    fairy.vertex_groups.clear()
    set_active(arm)
    fairy.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    mw = fairy.matrix_world.copy()
    fairy.parent = None
    fairy.matrix_world = mw
    for m in list(fairy.modifiers):
        if m.type == 'ARMATURE':
            fairy.modifiers.remove(m)
    m = fairy.modifiers.new('Armature', 'ARMATURE')
    m.object = arm
    fairy.modifiers.move(len(fairy.modifiers) - 1, 0)     # サブディビジョンより前

    deform = [b for b in arm.data.bones if b.use_deform]
    for b in deform:
        if b.name not in fairy.vertex_groups:
            fairy.vertex_groups.new(name=b.name)
    segs = {b.name: (arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local)
            for b in deform}

    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
        return (a + ab * t - p).length

    n = len(tail)
    tail_top = HIP_Z - 0.04
    for v in fairy.data.vertices:
        p = fairy.matrix_world @ v.co
        if p.z < tail_top:
            for g in list(v.groups):
                fairy.vertex_groups[g.group].remove([v.index])
            if p.z < HOLE_Z - 0.04:
                fairy.vertex_groups['DEF-tail_base'].add([v.index], 1.0, 'REPLACE')
                continue
            f = (p.z - HOLE_Z) / (HIP_Z - HOLE_Z) * n - 0.5
            i0 = math.floor(f)
            t = smoothstep(0.0, 1.0, f - i0)
            pairs = {}
            for i, w in ((i0, 1 - t), (i0 + 1, t)):
                name = ('DEF-tail_base' if p.z < HOLE_Z else tail[0]) if i < 0 else \
                       ('hips' if i >= n else tail[i])
                pairs[name] = pairs.get(name, 0) + w
            for bn, w in pairs.items():
                fairy.vertex_groups[bn].add([v.index], w, 'REPLACE')
        elif sum(g.weight for g in v.groups) < 1e-3:
            best = min(segs, key=lambda k: seg_dist(p, *segs[k]))
            fairy.vertex_groups[best].add([v.index], 1.0, 'REPLACE')


def parent_to_bone(child, arm, bone_name):
    mw = child.matrix_world.copy()
    child.parent = arm
    child.parent_type = 'BONE'
    child.parent_bone = bone_name
    bpy.context.view_layer.update()
    pb = arm.pose.bones[bone_name]
    parent_mat = arm.matrix_world @ pb.matrix @ Matrix.Translation(V((0, pb.length, 0)))
    child.matrix_parent_inverse = parent_mat.inverted()
    child.matrix_world = mw


# ================================================================ STOMACH (シェイプキー)
def build_stomach_keys(amount=0.15):
    st = obj('stomach_geo')
    ws = [st.matrix_world @ v.co for v in st.data.vertices]
    lo = V((min(p.x for p in ws), min(p.y for p in ws), min(p.z for p in ws)))
    hi = V((max(p.x for p in ws), max(p.y for p in ws), max(p.z for p in ws)))
    c_world = (lo + hi) / 2
    inv = st.matrix_world.inverted()
    c = inv @ c_world

    if st.data.shape_keys is None:
        st.shape_key_add(name='Basis', from_mix=False)
    keys = {}
    for name, sign in (('膨張', 1), ('収縮', -1)):
        kb = st.data.shape_keys.key_blocks.get(name) or st.shape_key_add(name=name, from_mix=False)
        for i, v in enumerate(st.data.vertices):
            kb.data[i].co = v.co + (v.co - c) * amount * sign
        kb.slider_min, kb.slider_max, kb.value = 0.0, 2.0, 0.0
        keys[name] = kb

    # 胃袋に絡む蔓を追従させる (同じ名前のシェイプキーを作り、胃袋のスライダーに連動)
    tris = [p.vertices[:] for p in st.data.polygons]
    bvh = BVHTree.FromPolygons(ws, tris)
    for n in ['stem_geo1', 'stem_geo2', 'stem_geo3', 'stem_geo4', 'stem_geo5']:
        o = obj(n)
        ks = []
        for i, v in enumerate(o.data.vertices):
            p = o.matrix_world @ v.co
            hit = bvh.find_nearest(p)
            if hit[0] is None:
                continue
            d = (hit[0] - p).length
            inside = hit[1].dot(p - hit[0]) < 0
            k = 1.0 if inside else 1.0 - smoothstep(0.08, 0.45, d)
            if k > 0:
                ks.append((i, k))
        if not ks:
            continue
        if o.data.shape_keys is None:
            o.shape_key_add(name='Basis', from_mix=False)
        oinv = o.matrix_world.inverted().to_3x3()
        for name, sign in (('膨張', 1), ('収縮', -1)):
            kb = o.data.shape_keys.key_blocks.get('胃袋_' + name) or \
                o.shape_key_add(name='胃袋_' + name, from_mix=False)
            for i, k in ks:
                p = o.matrix_world @ o.data.vertices[i].co
                kb.data[i].co = o.data.vertices[i].co + oinv @ ((p - c_world) * amount * sign * k)
            kb.slider_min, kb.slider_max = 0.0, 2.0
            fc = kb.driver_add('value')
            d = fc.driver
            d.type = 'AVERAGE'
            var = d.variables.new()
            var.type = 'SINGLE_PROP'
            var.targets[0].id_type = 'KEY'
            var.targets[0].id = st.data.shape_keys
            var.targets[0].data_path = f'key_blocks["{name}"].value'


# ================================================================ main
def main(out_path=None):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        mode('OBJECT')
    for n in ['fairy_rig', 'fairy_tail_curve']:
        if n in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[n])
    active = bpy.context.view_layer.objects.active
    frame = bpy.context.scene.frame_current

    get_coll(WGT_COLL, exclude=True)
    build_fairy()
    build_stomach_keys()

    bpy.context.scene.frame_current = frame
    if active:
        set_active(active)
    # 元ファイルで未使用 (ユーザー 0) のデータも保存時に消えないようにする
    for attr in ('images', 'materials', 'node_groups', 'textures', 'meshes', 'actions'):
        for b in getattr(bpy.data, attr):
            if b.users == 0 and not b.use_fake_user:
                b.use_fake_user = True
    if out_path:
        # 画像の相対パスを書き換えない (元ファイルと同じフォルダに保存すること)
        bpy.ops.wm.save_as_mainfile(filepath=out_path, compress=True, relative_remap=False)
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
