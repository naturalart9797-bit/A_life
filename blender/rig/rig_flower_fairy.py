"""
flower_v008.blend 用リグ自動セットアップ

使い方:
  - Blender で flower_v008.blend を開き、Scripting タブでこのファイルを開いて「実行」
  - またはコマンドライン:
      blender -b flower_v008.blend --python rig_flower_fairy.py -- out.blend

作るもの:
  flower_rig : 根(固定) / 茎上部 / 花の頭 / 胃袋(スケールで膨張・収縮)
  fairy_rig  : 標準的な人型の骨格 (FK)。root は花の中の口の底の穴。
               root → tail(蛇の柄) → hips → spine → chest → neck → head
               chest → shoulder → upper_arm → forearm → hand → 指
               flower_rig の flower_head にペアレントされるので花と一緒に動く
"""

import bpy
import sys
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


def new_armature(name, coll, location=(0, 0, 0)):
    data = bpy.data.armatures.new(name)
    data.display_type = 'OCTAHEDRAL'
    o = bpy.data.objects.new(name, data)
    o.location = V(location)
    o.show_in_front = True
    link_to(o, coll)
    bpy.context.view_layer.update()
    return o


def add_bone(arm, name, head, tail, parent=None, deform=True, roll_axis=None,
             connect=False):
    """head / tail はワールド座標で渡す"""
    inv = arm.matrix_world.inverted()
    eb = arm.data.edit_bones.new(name)
    eb.head = inv @ V(head)
    eb.tail = inv @ V(tail)
    if roll_axis is not None:
        eb.align_roll(inv.to_3x3() @ V(roll_axis))
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


def ensure_armature_mod(mesh_obj, arm):
    for m in list(mesh_obj.modifiers):
        if m.type == 'ARMATURE':
            mesh_obj.modifiers.remove(m)
    m = mesh_obj.modifiers.new('Armature', 'ARMATURE')
    m.object = arm
    mesh_obj.modifiers.move(len(mesh_obj.modifiers) - 1, 0)   # サブディブ等より前
    return m


def smoothstep(e0, e1, x):
    t = min(max((x - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def assign_weights(o, weights_per_vert):
    o.vertex_groups.clear()
    groups = {}
    for i, wd in enumerate(weights_per_vert):
        for bn, w in wd.items():
            if w <= 1e-4:
                continue
            g = groups.get(bn) or o.vertex_groups.new(name=bn)
            groups[bn] = g
            g.add([i], w, 'REPLACE')


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


# ================================================================ FLOWER
STOMACH_LOBES = {'stomach_lobe.R': (1, 0, 0), 'stomach_lobe.L': (-1, 0, 0),
                 'stomach_lobe.back': (0, 1, 0), 'stomach_lobe.front': (0, -1, 0),
                 'stomach_lobe.top': (0, 0, 1), 'stomach_lobe.bottom': (0, 0, -1)}


def build_flower(coll):
    arm = new_armature('flower_rig', coll)
    set_active(arm)
    mode('EDIT')

    add_bone(arm, 'flower_root', (0, 0, 0), (0, 0, 2.0))

    # 胃袋より上の茎
    stalk_pts = [(-0.30, -0.20, 11.6), (-0.20, -0.22, 12.6), (-0.12, -0.15, 13.6),
                 (-0.06, 0.00, 14.6), (0.02, 0.06, 15.6), (0.03, 0.03, 16.6)]
    prev = 'flower_root'
    stalk_names = []
    for i in range(len(stalk_pts) - 1):
        n = f'stalk.{i + 1:03d}'
        add_bone(arm, n, stalk_pts[i], stalk_pts[i + 1], parent=prev,
                 connect=(i > 0), roll_axis=(0, -1, 0))
        stalk_names.append(n)
        prev = n
    add_bone(arm, 'flower_head', stalk_pts[-1], (0.03, 0.03, 18.4), parent=prev,
             connect=True, roll_axis=(0, -1, 0))

    # 胃袋: stomach をスケール → 全体が膨張 / 収縮
    #       stomach_lobe.* をスケール → その方向だけ膨らむ / へこむ
    st = obj('stomach_geo')
    bb = [st.matrix_world @ V(c) for c in st.bound_box]
    c = (V((min(p.x for p in bb), min(p.y for p in bb), min(p.z for p in bb))) +
         V((max(p.x for p in bb), max(p.y for p in bb), max(p.z for p in bb)))) / 2
    add_bone(arm, 'stomach', c, c + V((0, 0, 2.2)), parent='flower_root',
             deform=False, roll_axis=(0, -1, 0))
    for n, d in STOMACH_LOBES.items():
        roll = (0, 0, 1) if abs(d[2]) < 0.5 else (0, -1, 0)
        add_bone(arm, n, c, c + V(d) * 1.4, parent='stomach', roll_axis=roll)
    mode('OBJECT')

    bone_coll(arm, 'Flower', ['flower_root', 'flower_head'] + stalk_names)
    bone_coll(arm, 'Stomach', ['stomach'] + list(STOMACH_LOBES))

    weight_flower(arm, stalk_pts, stalk_names, c)
    return arm


def weight_flower(arm, stalk_pts, stalk_names, center):
    joints = [p[2] for p in stalk_pts]
    chain = ['flower_root'] + stalk_names + ['flower_head']
    blend = 0.35

    def chain_weights(z):
        for j, zj in enumerate(joints):
            lower, upper = chain[j], chain[j + 1]
            if z < zj - blend:
                return {lower: 1.0}
            if z <= zj + blend:
                t = smoothstep(zj - blend, zj + blend, z)
                return {lower: 1 - t, upper: t}
        return {chain[-1]: 1.0}

    st = obj('stomach_geo')
    st_world = [st.matrix_world @ v.co for v in st.data.vertices]
    bvh = BVHTree.FromPolygons(st_world, [p.vertices[:] for p in st.data.polygons])
    dirs = {n: V(d) for n, d in STOMACH_LOBES.items()}

    def lobe_weights(p):
        d = p - center
        if d.length < 1e-6:
            return {n: 1 / 6 for n in dirs}
        d.normalize()
        w = {n: max(0.0, d.dot(a)) ** 2 for n, a in dirs.items()}
        s = sum(w.values()) or 1
        return {n: x / s for n, x in w.items() if x > 1e-4}

    assign_weights(st, [lobe_weights(p) for p in st_world])
    ensure_armature_mod(st, arm)

    # 茎: 根は固定、上部は茎の骨、胃袋に絡む蔓は胃袋と一緒に動く
    near_in, near_out = 0.08, 0.45
    for n in ['stem_geo1', 'stem_geo2', 'stem_geo3', 'stem_geo4', 'stem_geo5']:
        o = obj(n)
        ws = []
        for v in o.data.vertices:
            p = o.matrix_world @ v.co
            base = chain_weights(p.z)
            hit = bvh.find_nearest(p)
            dist = (hit[0] - p).length if hit[0] is not None else 1e9
            inside = hit[0] is not None and hit[1].dot(p - hit[0]) < 0
            k = 1.0 if inside else 1.0 - smoothstep(near_in, near_out, dist)
            if k > 0:
                wd = {b: x * (1 - k) for b, x in base.items()}
                for b, x in lobe_weights(p).items():
                    wd[b] = wd.get(b, 0) + x * k
            else:
                wd = base
            ws.append(wd)
        assign_weights(o, ws)
        ensure_armature_mod(o, arm)

    for n in ['petal_geo', 'mouth_geo']:
        o = obj(n)
        assign_weights(o, [{'flower_head': 1.0}] * len(o.data.vertices))
        ensure_armature_mod(o, arm)


# ================================================================ FAIRY
Y = 0.03            # 体の中心 (y)
HOLE_Z = 16.98      # 口(mouth_geo)の底の穴
HIP_Z = 20.0
N_TAIL = 12

# 左手の指 (付け根, 指先)。右手は x を反転
FINGERS = {
    'thumb':  ((0.497, -0.040), (0.509, -0.073, 20.280)),
    'index':  ((0.534, -0.046), (0.5747, -0.0628, 20.2748)),
    'middle': ((0.536, -0.031), (0.5801, -0.0398, 20.2687)),
    'ring':   ((0.535, -0.017), (0.5789, -0.0231, 20.2706)),
    'pinky':  ((0.532, 0.000), (0.5693, 0.0012, 20.2816)),
}


def mesh_center_near(verts, x, y, r=0.005, z_hint=None):
    """xy の近くにある頂点の z 平均 (指の中心線を取るため)"""
    s = [p for p in verts if (p.x - x) ** 2 + (p.y - y) ** 2 < r * r and
         (z_hint is None or abs(p.z - z_hint) < 0.04)]
    if not s:
        return None
    return sum(p.z for p in s) / len(s)


def build_fairy(coll, flower_arm):
    fairy = obj('fairy_geo')
    fverts = [fairy.matrix_world @ v.co for v in fairy.data.vertices]

    arm = new_armature('fairy_rig', coll, location=(0, Y, HOLE_Z))
    set_active(arm)
    mode('EDIT')

    # root (穴) : 下向きの小さい骨。穴より下の埋まっている部分を固定する
    add_bone(arm, 'root', (0, Y, HOLE_Z), (0, Y, HOLE_Z - 0.3), roll_axis=(0, -1, 0))

    # tail : 穴 → 腰 (蛇の部分)
    prev = 'root'
    tail_names = []
    for i in range(N_TAIL):
        z0 = HOLE_Z + (HIP_Z - HOLE_Z) * i / N_TAIL
        z1 = HOLE_Z + (HIP_Z - HOLE_Z) * (i + 1) / N_TAIL
        n = f'tail.{i + 1:03d}'
        add_bone(arm, n, (0, Y, z0), (0, Y, z1), parent=prev,
                 connect=(i > 0), roll_axis=(0, -1, 0))
        tail_names.append(n)
        prev = n

    # 体幹
    add_bone(arm, 'hips', (0, Y, 20.0), (0, Y, 20.20), parent=tail_names[-1],
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'spine', (0, Y, 20.20), (0, Y, 20.30), parent='hips',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'spine.001', (0, Y, 20.30), (0, Y, 20.40), parent='spine',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'chest', (0, Y, 20.40), (0, Y + 0.01, 20.58), parent='spine.001',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'neck', (0, Y + 0.01, 20.58), (0, 0.0, 20.665), parent='chest',
             connect=True, roll_axis=(0, -1, 0))
    add_bone(arm, 'head', (0, 0.0, 20.665), (0, 0.0, 20.95), parent='neck',
             connect=True, roll_axis=(0, -1, 0))

    # 腕・手・指
    for s, sx in (('L', 1), ('R', -1)):
        def P(x, y, z):
            return (x * sx, y, z)
        add_bone(arm, f'shoulder.{s}', P(0.02, 0.04, 20.555), P(0.11, 0.06, 20.54),
                 parent='chest', roll_axis=(0, 0, 1))
        add_bone(arm, f'upper_arm.{s}', P(0.11, 0.06, 20.54), P(0.30, 0.066, 20.448),
                 parent=f'shoulder.{s}', connect=True, roll_axis=(0, 0, 1))
        add_bone(arm, f'forearm.{s}', P(0.30, 0.066, 20.448), P(0.462, 0.0, 20.355),
                 parent=f'upper_arm.{s}', connect=True, roll_axis=(0, 0, 1))
        mid_base = FINGERS['middle'][0]
        mz = mesh_center_near(fverts, mid_base[0], mid_base[1], z_hint=20.31) or 20.31
        add_bone(arm, f'hand.{s}', P(0.462, 0.0, 20.355), P(mid_base[0], mid_base[1], mz),
                 parent=f'forearm.{s}', connect=True, roll_axis=(0, 0, 1))
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
                add_bone(arm, n, P(a.x, a.y, a.z), P(b.x, b.y, b.z), parent=prevf,
                         connect=(k > 0), roll_axis=(0, 0, 1))
                prevf = n
    mode('OBJECT')

    body = ['hips', 'spine', 'spine.001', 'chest', 'neck', 'head']
    arms = [f'{b}.{s}' for s in 'LR' for b in ('shoulder', 'upper_arm', 'forearm', 'hand')]
    fingers = [f'{f}.{k:02d}.{s}' for s in 'LR' for f in FINGERS for k in (1, 2, 3)]
    bone_coll(arm, 'Root & Tail', ['root'] + tail_names)
    bone_coll(arm, 'Body', body)
    bone_coll(arm, 'Arms', arms)
    bone_coll(arm, 'Fingers', fingers)

    # 花の頭と一緒に動く
    parent_to_bone(arm, flower_arm, 'flower_head')

    # ---- ウェイト: 自動ウェイト(ボーンヒート) → 柄と埋まった部分は z に沿って付け直す
    fairy.vertex_groups.clear()
    for m in list(fairy.modifiers):
        if m.type == 'ARMATURE':
            fairy.modifiers.remove(m)
    set_active(arm)
    fairy.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.parent_set(type='ARMATURE_AUTO')
    mw = fairy.matrix_world.copy()
    fairy.parent = None              # 花の骨にぶら下がっているので二重に動かないよう外す
    fairy.matrix_world = mw
    ensure_armature_mod(fairy, arm)
    fix_fairy_weights(fairy, arm, tail_names)

    for n in ['eye_L_geo', 'eye_R_geo']:
        parent_to_bone(obj(n), arm, 'head')
    return arm


def fix_fairy_weights(fairy, arm, tail_names):
    deform = [b for b in arm.data.bones if b.use_deform]
    segs = {b.name: (arm.matrix_world @ b.head_local, arm.matrix_world @ b.tail_local)
            for b in deform}
    for b in deform:
        if b.name not in fairy.vertex_groups:
            fairy.vertex_groups.new(name=b.name)

    def seg_dist(p, a, b):
        ab = b - a
        t = max(0.0, min(1.0, (p - a).dot(ab) / ab.length_squared))
        return (a + ab * t - p).length

    n_tail = len(tail_names)
    tail_top = HIP_Z - 0.05
    filled = 0
    for v in fairy.data.vertices:
        p = fairy.matrix_world @ v.co
        if p.z < tail_top:
            for g in list(v.groups):
                fairy.vertex_groups[g.group].remove([v.index])
            if p.z < HOLE_Z - 0.05:                 # 穴より下 (口の中・茎の中)
                fairy.vertex_groups['root'].add([v.index], 1.0, 'REPLACE')
                continue
            # 骨の中央で 100%、継ぎ目で隣とブレンド
            f = (p.z - HOLE_Z) / (HIP_Z - HOLE_Z) * n_tail - 0.5
            i0 = int(f // 1)
            t = smoothstep(0.0, 1.0, f - i0)
            pairs = {}
            for i, w in ((i0, 1 - t), (i0 + 1, t)):
                if i < 0:
                    name = 'root' if p.z < HOLE_Z else tail_names[0]
                elif i >= n_tail:
                    name = 'hips'
                else:
                    name = tail_names[i]
                pairs[name] = pairs.get(name, 0) + w
            for bn, w in pairs.items():
                fairy.vertex_groups[bn].add([v.index], w, 'REPLACE')
            continue
        if sum(g.weight for g in v.groups) < 1e-3:
            filled += 1
            best = min(segs, key=lambda n: seg_dist(p, *segs[n]))
            fairy.vertex_groups[best].add([v.index], 1.0, 'REPLACE')
    print(f'[rig] fairy: filled {filled} unweighted verts')


# ================================================================ main
def main(out_path=None):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        mode('OBJECT')
    # 元ファイルの未使用 Armature (どのメッシュにもバインドされていない仮ボーン)
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
    build_fairy(coll, flower)
    set_active(flower)
    if out_path:
        bpy.ops.wm.save_as_mainfile(filepath=out_path, compress=True)
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
