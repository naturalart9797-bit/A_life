# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bone rig for the butterfly proboscis.

Generate Rig turns the procedural (Geometry Nodes) proboscis into
  * a real mesh (all quads, auto UV) skinned to
  * a chain of deform bones (shorter towards the tip, max ~15 deg bend each)
  * inside a new or an existing armature (e.g. the butterfly body rig),
    parented to the head bone so it follows the head automatically.

One slider on the control bone drives everything (driver "simple expressions",
so no Python auto-run is needed and the animation bakes/exports normally):

  extend  0 = coiled (rest shape) ... 1 = uncoiled, tip on the target
  aim     0 = extend to the default straight-ish shape, 1 = reach the target

Reaching: the extended proboscis has a fixed bend profile (a "knee" plus a
slight distal curve) scaled by one bend amount. Because the chain length is
fixed, tip distance depends only on that bend amount; the inverse
(distance -> bend, chord angle) is fitted with polynomials at build time.
Drivers then compute yaw / pitch / bend from the target position measured in
the head's space, so the tip lands on the target exactly and stays there
while the head moves.
"""

import math

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from mathutils import Matrix, Vector

RIG_TAG = "proboscis_rig"
CTRL_PROPS = ("extend", "aim")
_POLY_DEGREE = 8
_MAX_EXPR = 255  # driver expressions are truncated beyond this


# --------------------------------------------------------------------------
# Shape math (mirrors the node group exactly)
# --------------------------------------------------------------------------

def _smoothstep(x, a, b):
    if b == a:
        return 0.0
    t = min(max((x - a) / (b - a), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


class ShapeParams:
    NAMES = ("Length", "Turns", "Inner Radius", "Straight Base", "Base Angle",
             "Extended Bend", "Uncoil Softness", "Resolution")

    def __init__(self, mod):
        from . import get_input
        v = {n: get_input(mod, n) for n in self.NAMES}
        self.length = max(float(v["Length"]), 1.0e-4)
        self.turns = max(float(v["Turns"]), 0.01)
        self.r_in = max(float(v["Inner Radius"]), 1.0e-5)
        self.base = float(v["Straight Base"])
        self.base_angle = float(v["Base Angle"])
        self.bend = float(v["Extended Bend"])
        self.soft = max(float(v["Uncoil Softness"]), 0.001)
        self.n = int(v["Resolution"])

    def theta(self, u, base_angle, bend):
        """Angle of each GN segment (segment j = point j -> j+1), as the nodes."""
        L, N = self.length, self.n
        ds = L / (N - 1)
        lb = self.base * L
        lc = max(L - lb, 1.0e-4)
        r0 = lc / (self.turns * math.pi) - self.r_in
        g = max((r0 - self.r_in) / self.turns, 1.0e-6)
        w = self.soft
        front = u * (1 + 2 * w) - w
        out, acc = [], 0.0
        for i in range(N):
            s = i / (N - 1)
            la = s * L
            ell = max(la - lb, 0.0)
            r_sq = max(r0 * r0 - g / math.pi * ell, self.r_in * self.r_in)
            k_coil = _smoothstep(la, lb * 0.4, lb + 1.0e-4) / math.sqrt(r_sq)
            k_ext = bend / L
            c = _smoothstep(s, front - w, front + w)
            k = k_ext + c * (k_coil - k_ext)
            out.append(base_angle + acc)
            acc += k * ds
        return out, ds

    @staticmethod
    def seg_theta(th, ds, l):
        return th[min(max(int(l / ds), 0), len(th) - 2)]

    def chord_angle(self, th, ds, a, b, n=64):
        cy = cz = 0.0
        for j in range(n):
            t = self.seg_theta(th, ds, a + (b - a) * (j + 0.5) / n)
            cy += math.cos(t)
            cz += math.sin(t)
        return math.atan2(cz, cy)

    def bone_bounds(self, max_angle, max_len_frac=1.0 / 32.0):
        """Split the length so no bone bends more than max_angle when coiled."""
        L = self.length
        th, ds = self.theta(0.0, 0.0, 0.0)

        def at(l):
            i = min(max(l / ds, 0.0), len(th) - 1.0)
            a = int(i)
            b = min(a + 1, len(th) - 1)
            return th[a] + (th[b] - th[a]) * (i - a)

        bounds, start, l = [0.0], 0.0, 0.0
        step, max_len = L / 4000.0, L * max_len_frac
        while l < L:
            l += step
            if abs(at(l) - at(start)) >= max_angle or l - start >= max_len:
                bounds.append(min(l, L))
                start = l
        if L - bounds[-1] < 1.0e-6:
            bounds[-1] = L
        else:
            bounds.append(L)
        return bounds

    def relative_angles(self, bounds, u, base_angle, bend):
        th, ds = self.theta(u, base_angle, bend)
        out, prev = [], 0.0
        for i in range(len(bounds) - 1):
            phi = self.chord_angle(th, ds, bounds[i], bounds[i + 1])
            out.append(math.remainder(phi - prev, math.tau))
            prev = phi
        return out


def _bend_profile(mids, L, knee, knee_width, knee_share):
    """Cumulative bend fraction (0..1) at each bone middle."""
    out = []
    for m in mids:
        s = m / L
        k = _smoothstep(s, knee - knee_width / 2, knee + knee_width / 2)
        d = max(s - knee, 0.0) / max(1.0 - knee, 1.0e-6)
        out.append(knee_share * k + (1.0 - knee_share) * d)
    return out


def _fit_reach(lens, profile, L, max_bend):
    """Fit bend(t) and chord(t), t = sqrt(1 - dist/L), for the given chain."""
    import numpy as np

    bends = np.linspace(0.0, max_bend, 2000)
    tips = []
    for b in bends:
        f = d = 0.0
        for ln, w in zip(lens, profile):
            f += ln * math.cos(b * w)
            d += ln * math.sin(b * w)
        tips.append((f, d))
    tips = np.array(tips)
    dist = np.hypot(tips[:, 0], tips[:, 1])
    chord = np.arctan2(tips[:, 1], tips[:, 0])
    # keep the monotonic part (distance must shrink as the bend grows)
    stop = len(dist)
    for i in range(1, len(dist)):
        if dist[i] >= dist[i - 1]:
            stop = i
            break
    bends, dist, chord = bends[:stop], dist[:stop], chord[:stop]
    t = np.sqrt(np.clip(1.0 - dist / L, 0.0, 1.0))
    c_bend = np.polyfit(t, bends, _POLY_DEGREE)
    c_chord = np.polyfit(t, chord, _POLY_DEGREE)
    err = max(float(np.abs(np.polyval(c_bend, t) - bends).max()),
              float(np.abs(np.polyval(c_chord, t) - chord).max()))
    return [float(c) for c in c_bend], [float(c) for c in c_chord], float(dist.min() / L), err


def _horner(coeffs, var="t"):
    expr = f"{coeffs[0]:.6g}"
    for c in coeffs[1:]:
        expr = f"({expr}) * {var} + {c:.6g}"
    return expr


# --------------------------------------------------------------------------
# Driver helpers
# --------------------------------------------------------------------------

def _add_driver(arm, path, index, expression, prop_vars=(), loc_bone=None, ctrl=None):
    fcurve = arm.driver_add(path, index) if index is not None else arm.driver_add(path)
    drv = fcurve.driver
    drv.type = "SCRIPTED"
    for v in list(drv.variables):
        drv.variables.remove(v)
    if loc_bone:
        for name, axis in (("x", "LOC_X"), ("y", "LOC_Y"), ("z", "LOC_Z")):
            var = drv.variables.new()
            var.name = name
            var.type = "TRANSFORMS"
            tgt = var.targets[0]
            tgt.id = arm
            tgt.bone_target = loc_bone
            tgt.transform_type = axis
            tgt.transform_space = "LOCAL_SPACE"
    for name, prop in prop_vars:
        var = drv.variables.new()
        var.name = name
        var.type = "SINGLE_PROP"
        var.targets[0].id = arm
        var.targets[0].data_path = f'pose.bones["{ctrl}"]["{prop}"]'
    drv.expression = expression
    if len(expression) >= _MAX_EXPR or not drv.is_simple_expression:
        raise RuntimeError(f"driver expression not usable: {expression}")
    return drv


def _set_prop(pbone, name, value, description="", min_value=None, max_value=None):
    pbone[name] = value
    try:
        ui = pbone.id_properties_ui(name)
        kw = {"description": description}
        if min_value is not None:
            kw.update(min=min_value, soft_min=min_value)
        if max_value is not None:
            kw.update(max=max_value, soft_max=max_value)
        ui.update(**kw)
    except (AttributeError, TypeError):
        pass


def _bone_collection(arm_data, name, visible=True):
    colls = getattr(arm_data, "collections", None)
    if colls is None:
        return None
    coll = colls.get(name) or colls.new(name)
    coll.is_visible = visible
    return coll


# --------------------------------------------------------------------------
# Rig lookup (used by the UI)
# --------------------------------------------------------------------------

def find_rig_controls(obj):
    """Return (armature object, control pose bone) for a proboscis rig."""
    if obj is None:
        return None, None
    arm = None
    if obj.type == "ARMATURE":
        arm = obj
    elif obj.type == "MESH":
        for mod in obj.modifiers:
            if mod.type == "ARMATURE" and mod.object is not None:
                arm = mod.object
                break
    if arm is None or arm.pose is None:
        return None, None
    for pb in arm.pose.bones:
        if pb.get(RIG_TAG):
            return arm, pb
    return arm, None


def rig_target_constraint(arm, ctrl):
    aim = arm.pose.bones.get(ctrl.get("aim_bone", ""))
    if aim is None:
        return None
    return next((c for c in aim.constraints if c.type == "COPY_LOCATION"), None)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class OBJECT_OT_proboscis_generate_rig(Operator):
    """口吻をボーンで動くメッシュにする（頭のボーンの子・伸ばすと目標へ届く）"""
    bl_idname = "object.proboscis_generate_rig"
    bl_label = "Generate Rig"
    bl_options = {"REGISTER", "UNDO"}

    armature_name: StringProperty(
        name="Armature",
        description="追加先のアーマチュア（蝶の本体のリグ）。空なら新しく作ります")
    parent_bone: StringProperty(
        name="Head Bone", description="口吻の親にするボーン（頭）")
    prefix: StringProperty(name="Bone Name", default="proboscis",
                           description="作るボーンの名前の頭")
    max_bend: FloatProperty(name="Max Bend per Bone", default=math.radians(15.0),
                            min=math.radians(3.0), max=math.radians(45.0),
                            subtype="ANGLE",
                            description="巻いたとき 1 本のボーンが曲がる最大角度"
                                        "（小さいほどなめらか・本数が増える）")
    knee: FloatProperty(name="Knee Position", default=0.45, min=0.15, max=0.8,
                        subtype="FACTOR",
                        description="伸ばしたときに曲がる「ひざ」の位置（根元から）")
    knee_width: FloatProperty(name="Knee Width", default=0.15, min=0.02, max=0.5,
                              subtype="FACTOR", description="ひざの曲がりの幅")
    knee_share: FloatProperty(name="Knee Share", default=0.85, min=0.0, max=1.0,
                              subtype="FACTOR",
                              description="曲がりのうち、ひざが受け持つ割合（残りは先端側のゆるいカーブ）")
    create_target: BoolProperty(name="Create Target", default=True,
                                description="伸ばした先の目標（Empty）を作る")
    auto_uv: BoolProperty(name="Auto Seams + UV Unwrap", default=True)

    @classmethod
    def poll(cls, context):
        from . import find_proboscis_modifier
        return find_proboscis_modifier(context.active_object) is not None

    def invoke(self, context, event):
        obj = context.active_object
        par = obj.parent
        if par is not None and par.type == "ARMATURE" and not self.armature_name:
            self.armature_name = par.name
            if obj.parent_type == "BONE":
                self.parent_bone = obj.parent_bone
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "armature_name", bpy.data, "objects")
        arm = bpy.data.objects.get(self.armature_name)
        if arm is not None and arm.type == "ARMATURE":
            layout.prop_search(self, "parent_bone", arm.data, "bones")
        layout.prop(self, "prefix")
        layout.prop(self, "max_bend")
        col = layout.column(align=True)
        col.prop(self, "knee")
        col.prop(self, "knee_width")
        col.prop(self, "knee_share")
        layout.prop(self, "create_target")
        layout.prop(self, "auto_uv")

    # ------------------------------------------------------------------
    def execute(self, context):
        from . import find_proboscis_modifier, set_input, get_input
        src = context.active_object
        mod = find_proboscis_modifier(src)
        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        arm = None
        if self.armature_name:
            arm = bpy.data.objects.get(self.armature_name)
            if arm is None or arm.type != "ARMATURE":
                self.report({"ERROR"}, f"アーマチュアが見つかりません: {self.armature_name}")
                return {"CANCELLED"}
            if self.parent_bone and self.parent_bone not in arm.data.bones:
                self.report({"ERROR"}, f"ボーンが見つかりません: {self.parent_bone}")
                return {"CANCELLED"}
            if any(b.name.startswith(self.prefix + ".") for b in arm.data.bones):
                self.report({"ERROR"}, f"'{self.prefix}' のボーンが既にあります。名前を変えてください")
                return {"CANCELLED"}

        params = ShapeParams(mod)
        L = params.length
        bounds = params.bone_bounds(self.max_bend)
        nb = len(bounds) - 1
        lens = [bounds[i + 1] - bounds[i] for i in range(nb)]
        mids = [(bounds[i] + bounds[i + 1]) / 2 for i in range(nb)]
        coil = params.relative_angles(bounds, 0.0, params.base_angle, params.bend)
        ext = params.relative_angles(bounds, 1.0, params.base_angle, params.bend)
        profile = _bend_profile(mids, L, self.knee, self.knee_width, self.knee_share)
        c_bend, c_chord, r_min, fit_err = _fit_reach(lens, profile, L, math.radians(170.0))

        # --- where the proboscis sits, in the armature's rest pose ---------
        rest_states = []
        for a in {arm, src.parent if src.parent and src.parent.type == "ARMATURE" else None}:
            if a is not None:
                rest_states.append((a, a.data.pose_position))
                a.data.pose_position = "REST"
        context.view_layer.update()
        src_world = src.matrix_world.copy()
        for a, state in rest_states:
            a.data.pose_position = state
        context.view_layer.update()

        # --- straight rest mesh from the node group --------------------------
        keep = {n: get_input(mod, n) for n in ("Uncoil", "Base Angle", "Extended Bend",
                                               "Wiggle Amount", "Use Edit Mesh")}
        for n, v in (("Uncoil", 1.0), ("Base Angle", 0.0), ("Extended Bend", 0.0),
                     ("Wiggle Amount", 0.0), ("Use Edit Mesh", False)):
            set_input(mod, n, v)
        src.update_tag()
        depsgraph = context.evaluated_depsgraph_get()
        depsgraph.update()
        mesh = bpy.data.meshes.new_from_object(src.evaluated_get(depsgraph),
                                               preserve_all_data_layers=True,
                                               depsgraph=depsgraph)
        for n, v in keep.items():
            set_input(mod, n, v)
        src.update_tag()
        mesh.name = f"{src.name}_rigged"
        from . import _clean_material_slots, mark_proboscis_seams
        _clean_material_slots(mesh)

        # --- armature ----------------------------------------------------------
        if arm is None:
            arm_data = bpy.data.armatures.new(f"{src.name}_rig")
            arm = bpy.data.objects.new(f"{src.name}_rig", arm_data)
            context.collection.objects.link(arm)
            arm.matrix_world = Matrix.LocRotScale(*src_world.decompose()[:2], Vector((1, 1, 1)))
            arm.show_in_front = True
        to_arm = arm.matrix_world.inverted() @ src_world
        scale = (to_arm.to_scale().x + to_arm.to_scale().y + to_arm.to_scale().z) / 3.0
        up = (to_arm.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()

        names = [f"{self.prefix}.{i:03d}" for i in range(nb)]
        ctrl_name = f"{self.prefix}_ctrl"
        aim_name = f"MCH-{self.prefix}_aim"

        for o in context.selected_objects:
            o.select_set(False)
        arm.hide_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
        bpy.ops.object.mode_set(mode="EDIT")
        ebones = arm.data.edit_bones
        parent = ebones.get(self.parent_bone) if self.parent_bone else None
        deform_coll = _bone_collection(arm.data, "Proboscis")
        ctrl_coll = _bone_collection(arm.data, "Proboscis Controls")
        mch_coll = _bone_collection(arm.data, "Proboscis MCH", visible=False)

        def P(local):
            return to_arm @ Vector(local)

        prev = parent
        for i, name in enumerate(names):
            eb = ebones.new(name)
            eb.head = P((0.0, bounds[i], 0.0))
            eb.tail = P((0.0, bounds[i + 1], 0.0))
            eb.align_roll(up)
            eb.parent = prev
            eb.use_connect = i > 0
            eb.use_deform = True
            if deform_coll:
                deform_coll.assign(eb)
            prev = eb
        aim = ebones.new(aim_name)
        aim.head = P((0.0, 0.0, 0.0))
        aim.tail = P((0.0, bounds[1], 0.0))
        aim.align_roll(up)
        aim.parent = parent
        aim.use_deform = False
        if mch_coll:
            mch_coll.assign(aim)
        ctrl = ebones.new(ctrl_name)
        ctrl.head = P((0.0, -0.05 * L, 0.15 * L))
        ctrl.tail = P((0.0, 0.10 * L, 0.15 * L))
        ctrl.align_roll(up)
        ctrl.parent = parent
        ctrl.use_deform = False
        if ctrl_coll:
            ctrl_coll.assign(ctrl)
        bpy.ops.object.mode_set(mode="OBJECT")

        pbones = arm.pose.bones
        cpb = pbones[ctrl_name]
        _set_prop(cpb, RIG_TAG, True)
        _set_prop(cpb, "aim_bone", aim_name)
        _set_prop(cpb, "root_bone", names[0])
        _set_prop(cpb, "extend", 0.0, "0 = 巻いた状態 / 1 = 伸ばして目標に届く", 0.0, 1.0)
        _set_prop(cpb, "aim", 1.0, "1 = 目標に向かって伸ばす / 0 = 決まった形に伸ばす", 0.0, 1.0)
        for p in ("_yaw", "_beta", "_tq", "_bend", "_alpha"):
            cpb[p] = 0.0
        cpb["reach_length"] = L * scale
        cpb["reach_min"] = r_min
        cpb.lock_location = cpb.lock_rotation = cpb.lock_scale = (True, True, True)

        # --- target --------------------------------------------------------
        target = None
        if self.create_target:
            target = bpy.data.objects.new(f"{self.prefix}_target", None)
            target.empty_display_type = "SPHERE"
            target.empty_display_size = 0.03 * L * src_world.to_scale().x
            context.collection.objects.link(target)
            # default spot: the tip of the default extended shape
            ang, pos = 0.0, Vector((0.0, 0.0, 0.0))
            for ln, d in zip(lens, ext):
                ang += d
                pos += Vector((0.0, math.cos(ang), -math.sin(ang))) * ln
            target.location = src_world @ pos
        con = pbones[aim_name].constraints.new("COPY_LOCATION")
        con.name = "Proboscis Target"
        con.target = target

        # --- drivers -------------------------------------------------------
        Ls = L * scale
        C = ctrl_name
        _add_driver(arm, f'pose.bones["{C}"]["_yaw"]', None, "atan2(-x, y)",
                    loc_bone=aim_name, ctrl=C)
        _add_driver(arm, f'pose.bones["{C}"]["_beta"]', None, "atan2(-z, sqrt(x*x + y*y))",
                    loc_bone=aim_name, ctrl=C)
        _add_driver(arm, f'pose.bones["{C}"]["_tq"]', None,
                    f"sqrt(1 - clamp(sqrt(x*x + y*y + z*z) / {Ls:.6g}, {r_min + 1e-4:.6f}, 1))",
                    loc_bone=aim_name, ctrl=C)
        _add_driver(arm, f'pose.bones["{C}"]["_bend"]', None, _horner(c_bend),
                    [("t", "_tq")], ctrl=C)
        _add_driver(arm, f'pose.bones["{C}"]["_alpha"]', None, f"b - ({_horner(c_chord)})",
                    [("t", "_tq"), ("b", "_beta")], ctrl=C)

        w = params.soft
        bone_vars = [("e", "extend"), ("m", "aim"), ("g", "_bend"), ("a", "_alpha")]
        for i, name in enumerate(names):
            pb = pbones[name]
            pb.rotation_mode = "XYZ"
            pb.lock_location = (True, True, True)
            coiled = (f"smoothstep(e * {1 + 2 * w:.4f} - {2 * w:.4f}, e * {1 + 2 * w:.4f}, "
                      f"{mids[i] / L:.5f})")
            if i == 0:
                reach = f"a + g * {profile[0]:.6f}"
            else:
                reach = f"g * {profile[i] - profile[i - 1]:.6f}"
            _add_driver(arm, f'pose.bones["{name}"].rotation_euler', 0,
                        f"-lerp(lerp({ext[i]:.6f}, {reach}, m), {coil[i]:.6f}, {coiled})",
                        bone_vars, ctrl=C)
        _add_driver(arm, f'pose.bones["{names[0]}"].rotation_euler', 2,
                    "w * m * smoothstep(0, 1, e)",
                    [("e", "extend"), ("m", "aim"), ("w", "_yaw")], ctrl=C)

        # --- mesh object -------------------------------------------------------
        mesh_obj = bpy.data.objects.new(f"{src.name}_mesh", mesh)
        context.collection.objects.link(mesh_obj)
        mesh_obj.parent = arm
        mesh_obj.matrix_world = src_world
        groups = [mesh_obj.vertex_groups.new(name=n) for n in names]
        for vi, v in enumerate(mesh.vertices):
            y = v.co.y
            if y <= mids[0]:
                groups[0].add([vi], 1.0, "REPLACE")
            elif y >= mids[-1]:
                groups[-1].add([vi], 1.0, "REPLACE")
            else:
                j = next(j for j in range(nb - 1) if y < mids[j + 1])
                t = (y - mids[j]) / (mids[j + 1] - mids[j])
                groups[j].add([vi], 1.0 - t, "REPLACE")
                groups[j + 1].add([vi], t, "REPLACE")
        amod = mesh_obj.modifiers.new("Armature", "ARMATURE")
        amod.object = arm

        if self.auto_uv and mark_proboscis_seams(mesh):
            if not mesh.uv_layers:
                mesh.uv_layers.new(name="UVMap")
            bpy.ops.object.mode_set(mode="OBJECT")
            arm.select_set(False)
            mesh_obj.select_set(True)
            context.view_layer.objects.active = mesh_obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.unwrap(method="ANGLE_BASED", margin=0.002)
            bpy.ops.object.mode_set(mode="OBJECT")
            mesh_obj.select_set(False)

        # the procedural source is no longer needed for the animation
        src.hide_set(True)
        src.hide_render = True

        for o in context.selected_objects:
            o.select_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
        context.view_layer.update()
        self.report({"INFO"}, f"{nb} bones / fit error {math.degrees(fit_err):.3f} deg")
        return {"FINISHED"}


class POSE_OT_proboscis_rig_drink(Operator):
    """伸ばして目標へ → 飲む → 巻き戻す のキーフレームを打つ（リグ用）"""
    bl_idname = "pose.proboscis_rig_drink"
    bl_label = "Add Drinking Keys"
    bl_options = {"REGISTER", "UNDO"}

    start_frame: IntProperty(name="Start Frame", default=1)
    extend_frames: IntProperty(name="Extend", default=24, min=1)
    drink_frames: IntProperty(name="Drink", default=60, min=0)
    retract_frames: IntProperty(name="Retract", default=36, min=1)

    @classmethod
    def poll(cls, context):
        return find_rig_controls(context.active_object)[1] is not None

    def invoke(self, context, event):
        self.start_frame = context.scene.frame_current
        return self.execute(context)

    def execute(self, context):
        arm, ctrl = find_rig_controls(context.active_object)
        path = f'pose.bones["{ctrl.name}"]["extend"]'
        f0 = self.start_frame
        f1 = f0 + self.extend_frames
        f2 = f1 + self.drink_frames
        f3 = f2 + self.retract_frames
        for frame, value in ((f0, 0.0), (f1, 1.0), (f2, 1.0), (f3, 0.0)):
            ctrl["extend"] = value
            arm.keyframe_insert(data_path=path, frame=frame)
        context.scene.frame_set(context.scene.frame_current)
        self.report({"INFO"}, f"frames {f0}-{f3}")
        return {"FINISHED"}


def draw_rig_panel(layout, context):
    """Panel section for a selected proboscis rig. Returns True if drawn."""
    arm, ctrl = find_rig_controls(context.active_object)
    if ctrl is None:
        return False
    box = layout.box()
    box.label(text=f"Proboscis Rig: {arm.name}", icon="ARMATURE_DATA")
    col = box.column(align=True)
    col.prop(ctrl, '["extend"]', text="Extend", slider=True)
    col.prop(ctrl, '["aim"]', text="Aim at Target", slider=True)
    con = rig_target_constraint(arm, ctrl)
    if con is not None:
        box.prop(con, "target", text="Target")
        root = arm.pose.bones.get(ctrl.get("root_bone", ""))
        if con.target is not None and root is not None:
            base = arm.matrix_world @ root.head
            ratio = (con.target.matrix_world.translation - base).length / max(
                ctrl.get("reach_length", 1.0) * arm.matrix_world.to_scale().x, 1e-9)
            icon = "CHECKMARK" if 0.55 <= ratio <= 0.95 else "ERROR"
            box.label(text=f"距離 / 長さ = {ratio:.2f}（0.55〜0.95 が自然）", icon=icon)
            if ratio > 1.0:
                box.label(text="届きません：頭を近づけるか目標を移動")
            elif ratio < ctrl.get("reach_min", 0.3) + 0.05:
                box.label(text="近すぎます：頭を離してください")
    box.label(text="Extend の上で I キー → キーフレーム", icon="KEYFRAME")
    box.operator(POSE_OT_proboscis_rig_drink.bl_idname, icon="MOD_FLUID")
    return True


classes = (
    OBJECT_OT_proboscis_generate_rig,
    POSE_OT_proboscis_rig_drink,
)
