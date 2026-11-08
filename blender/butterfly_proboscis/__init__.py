# SPDX-License-Identifier: GPL-3.0-or-later
"""
Butterfly Proboscis (蝶の口吻) add-on.

Geometry Nodes で「くるくる巻いた蝶の口吻」を生成します。
モディファイアの "Uncoil"（0 = 巻いている / 1 = 伸びている）をキーフレームすれば、
水や蜜を飲むときに根元からほどけて伸びるアニメーションが作れます。

形のしくみ:
  口吻を N 個の点で表し、各点の「曲率」を決めて積分（Accumulate Field）して形を作ります。
  - 巻いた状態: アルキメデス螺旋になるように曲率を決定（全長は常に一定）
  - 伸びた状態: ゆるいカーブ（Extended Bend）+ 先端の探るような揺れ（Wiggle）
  - Uncoil の値に応じて、根元から先端に向かって 巻き→伸び に切り替わる
"""

bl_info = {
    "name": "Butterfly Proboscis",
    "author": "naturalart9797-bit",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Add > Mesh > Butterfly Proboscis / Sidebar (N) > Butterfly",
    "description": "Procedural coiled butterfly proboscis that can uncoil for drinking",
    "category": "Add Mesh",
}

import math

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Operator, Panel

GROUP_NAME = "Butterfly Proboscis"
GROUP_TAG = "butterfly_proboscis_version"
GROUP_VERSION = 1
MATERIAL_NAME = "Butterfly Proboscis"


# --------------------------------------------------------------------------
# Node tree builder helpers
# --------------------------------------------------------------------------

class _Builder:
    def __init__(self, ng):
        self.ng = ng
        self.nodes = ng.nodes
        self.links = ng.links

    def node(self, bl_idname, **props):
        n = self.nodes.new(bl_idname)
        for key, value in props.items():
            setattr(n, key, value)
        return n

    def feed(self, socket, value):
        if isinstance(value, bpy.types.NodeSocket):
            self.links.new(value, socket)
        elif value is not None:
            socket.default_value = value

    def math(self, op, a, b=None, c=None, label=""):
        n = self.node("ShaderNodeMath", operation=op)
        if label:
            n.label = label
        self.feed(n.inputs[0], a)
        self.feed(n.inputs[1], b)
        self.feed(n.inputs[2], c)
        return n.outputs[0]

    def add(self, a, b):
        return self.math("ADD", a, b)

    def sub(self, a, b):
        return self.math("SUBTRACT", a, b)

    def mul(self, a, b):
        return self.math("MULTIPLY", a, b)

    def div(self, a, b):
        return self.math("DIVIDE", a, b)

    def maximum(self, a, b):
        return self.math("MAXIMUM", a, b)

    def map_range(self, value, from_min, from_max, to_min=0.0, to_max=1.0,
                  smooth=False, label=""):
        n = self.node("ShaderNodeMapRange")
        n.data_type = "FLOAT"
        n.interpolation_type = "SMOOTHSTEP" if smooth else "LINEAR"
        n.clamp = True
        if label:
            n.label = label
        self.feed(n.inputs["Value"], value)
        self.feed(n.inputs["From Min"], from_min)
        self.feed(n.inputs["From Max"], from_max)
        self.feed(n.inputs["To Min"], to_min)
        self.feed(n.inputs["To Max"], to_max)
        return n.outputs["Result"]

    def accumulate_trailing(self, value, label=""):
        """Running sum of `value` over curve points, excluding the current point."""
        n = self.node("GeometryNodeAccumulateField")
        n.data_type = "FLOAT"
        n.domain = "POINT"
        if label:
            n.label = label
        in_sock = next(s for s in n.inputs if s.name == "Value" and s.type == "VALUE")
        out_sock = next(s for s in n.outputs if s.name == "Trailing" and s.type == "VALUE")
        self.feed(in_sock, value)
        return out_sock

    def layout(self):
        """Very small auto layout: columns by longest path from the inputs."""
        depth = {n: 0 for n in self.nodes}
        for _ in range(len(self.nodes)):
            changed = False
            for link in self.links:
                d = depth[link.from_node] + 1
                if d > depth[link.to_node]:
                    depth[link.to_node] = d
                    changed = True
            if not changed:
                break
        rows = {}
        for n in self.nodes:
            col = depth[n]
            row = rows.get(col, 0)
            rows[col] = row + 1
            n.location = (col * 220.0, -row * 190.0)


def _new_input(ng, name, socket_type, default=None, min_value=None, max_value=None,
               subtype=None, description=""):
    sock = ng.interface.new_socket(name, in_out="INPUT", socket_type=socket_type,
                                   description=description)
    if subtype:
        try:
            sock.subtype = subtype
        except (AttributeError, TypeError):
            pass
    if default is not None:
        sock.default_value = default
    if min_value is not None:
        sock.min_value = min_value
    if max_value is not None:
        sock.max_value = max_value
    return sock


def _build_node_group():
    ng = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    ng[GROUP_TAG] = GROUP_VERSION
    ng["is_butterfly_proboscis"] = True

    _new_input(ng, "Uncoil", "NodeSocketFloat", 0.0, 0.0, 1.0, "FACTOR",
               "0 = くるくる巻いた状態 / 1 = まっすぐ伸びた状態（キーフレーム可）")
    _new_input(ng, "Length", "NodeSocketFloat", 1.0, 0.001, 1000.0, "DISTANCE",
               "口吻の全長（巻いていても伸びていても同じ）")
    _new_input(ng, "Turns", "NodeSocketFloat", 3.0, 0.1, 20.0, None,
               "巻き数")
    _new_input(ng, "Inner Radius", "NodeSocketFloat", 0.01, 0.0001, 100.0, "DISTANCE",
               "螺旋の中心（先端側）の半径")
    _new_input(ng, "Straight Base", "NodeSocketFloat", 0.12, 0.0, 0.9, "FACTOR",
               "根元のまっすぐな部分の割合")
    _new_input(ng, "Base Angle", "NodeSocketFloat", math.radians(15.0),
               -math.pi, math.pi, "ANGLE",
               "根元の向き（0 = 前方 +Y、正の値で下向き）")
    _new_input(ng, "Extended Bend", "NodeSocketFloat", math.radians(25.0),
               -math.pi * 2, math.pi * 2, "ANGLE",
               "伸ばしたときの全体の反り（正で下向き）")
    _new_input(ng, "Uncoil Softness", "NodeSocketFloat", 0.15, 0.001, 1.0, "FACTOR",
               "ほどける境目のなめらかさ")
    _new_input(ng, "Wiggle Amount", "NodeSocketFloat", 0.0, -10.0, 10.0, None,
               "伸びた部分の揺れの強さ（水を探る動き）")
    _new_input(ng, "Wiggle Frequency", "NodeSocketFloat", 1.5, 0.0, 20.0, None,
               "揺れの波の数")
    _new_input(ng, "Wiggle Phase", "NodeSocketFloat", 0.0, -1.0e4, 1.0e4, "ANGLE",
               "揺れの位相（アニメーションさせると波が動く）")
    _new_input(ng, "Base Radius", "NodeSocketFloat", 0.01, 0.0, 100.0, "DISTANCE",
               "根元の太さ（半径）")
    _new_input(ng, "Tip Radius", "NodeSocketFloat", 0.004, 0.0, 100.0, "DISTANCE",
               "先端の太さ（半径）")
    _new_input(ng, "Resolution", "NodeSocketInt", 240, 8, 5000, None,
               "長さ方向の分割数")
    _new_input(ng, "Profile Resolution", "NodeSocketInt", 12, 3, 256, None,
               "断面の分割数")
    _new_input(ng, "Material", "NodeSocketMaterial", None, None, None, None, "マテリアル")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")

    b = _Builder(ng)
    gi = b.node("NodeGroupInput")
    go = b.node("NodeGroupOutput")
    I = gi.outputs  # noqa: E741

    # --- parameterisation ------------------------------------------------
    n_minus_1 = b.math("SUBTRACT", I["Resolution"], 1.0)
    index = b.node("GeometryNodeInputIndex").outputs["Index"]
    s = b.div(index, n_minus_1)                     # 0 .. 1 along the proboscis
    length = b.maximum(I["Length"], 1.0e-4)
    ds = b.div(length, n_minus_1)                   # segment length
    ell_abs = b.mul(s, length)                      # arc length from the base

    # --- coiled curvature: Archimedean spiral with constant length -------
    #   r0 + r_in = Lc / (pi * T),  gap g = (r0 - r_in) / T
    #   r(l)^2 = r0^2 - (g / pi) * l   ->   curvature = 1 / r(l)
    base_len = b.mul(I["Straight Base"], length)
    coil_len = b.maximum(b.sub(length, base_len), 1.0e-4)
    turns = b.maximum(I["Turns"], 0.01)
    r_in = b.maximum(I["Inner Radius"], 1.0e-5)
    r0 = b.sub(b.div(coil_len, b.mul(turns, math.pi)), r_in)
    gap = b.maximum(b.div(b.sub(r0, r_in), turns), 1.0e-6)
    ell = b.maximum(b.sub(ell_abs, base_len), 0.0)
    r_sq = b.maximum(b.sub(b.mul(r0, r0), b.mul(b.div(gap, math.pi), ell)),
                     b.mul(r_in, r_in))
    ramp = b.map_range(ell_abs, b.mul(base_len, 0.4), b.add(base_len, 1.0e-4),
                       smooth=True, label="Straight Base Ramp")
    k_coil = b.mul(b.div(1.0, b.math("SQRT", r_sq)), ramp)

    # --- extended curvature: gentle bend + wiggle -------------------------
    k_bend = b.div(I["Extended Bend"], length)
    wave = b.math("SINE", b.add(b.mul(s, b.mul(I["Wiggle Frequency"], math.tau)),
                                I["Wiggle Phase"]))
    k_wiggle = b.div(b.mul(b.mul(wave, s), I["Wiggle Amount"]), length)
    k_ext = b.add(k_bend, k_wiggle)

    # --- blend: uncoils progressively from the base to the tip -----------
    soft = b.maximum(I["Uncoil Softness"], 0.001)
    front = b.sub(b.mul(I["Uncoil"], b.add(b.mul(soft, 2.0), 1.0)), soft)
    coiled = b.map_range(s, b.sub(front, soft), b.add(front, soft),
                         smooth=True, label="Coiled Factor")
    k = b.add(k_ext, b.mul(coiled, b.sub(k_coil, k_ext)))

    # --- integrate curvature -> angle -> position (YZ plane) -------------
    theta = b.add(I["Base Angle"], b.accumulate_trailing(b.mul(k, ds), "Angle"))
    y = b.accumulate_trailing(b.mul(b.math("COSINE", theta), ds), "Y")
    z = b.mul(b.accumulate_trailing(b.mul(b.math("SINE", theta), ds), "Z"), -1.0)
    pos = b.node("ShaderNodeCombineXYZ")
    b.feed(pos.inputs["Y"], y)
    b.feed(pos.inputs["Z"], z)

    # --- geometry ---------------------------------------------------------
    line = b.node("GeometryNodeCurvePrimitiveLine")
    resample = b.node("GeometryNodeResampleCurve")
    b.feed(resample.inputs["Curve"], line.outputs["Curve"])
    b.feed(resample.inputs["Count"], I["Resolution"])

    set_pos = b.node("GeometryNodeSetPosition")
    b.feed(set_pos.inputs["Geometry"], resample.outputs["Curve"])
    b.feed(set_pos.inputs["Position"], pos.outputs["Vector"])

    taper = b.map_range(s, 0.0, 1.0, I["Base Radius"], I["Tip Radius"], label="Taper")
    set_radius = b.node("GeometryNodeSetCurveRadius")
    b.feed(set_radius.inputs["Curve"], set_pos.outputs["Geometry"])
    b.feed(set_radius.inputs["Radius"], taper)

    profile = b.node("GeometryNodeCurvePrimitiveCircle")
    b.feed(profile.inputs["Resolution"], I["Profile Resolution"])
    profile.inputs["Radius"].default_value = 1.0

    to_mesh = b.node("GeometryNodeCurveToMesh")
    b.feed(to_mesh.inputs["Curve"], set_radius.outputs["Curve"])
    b.feed(to_mesh.inputs["Profile Curve"], profile.outputs["Curve"])
    if "Fill Caps" in to_mesh.inputs:
        to_mesh.inputs["Fill Caps"].default_value = True
    if "Scale" in to_mesh.inputs:  # Blender 4.2+: explicit profile scale
        b.feed(to_mesh.inputs["Scale"], taper)

    smooth = b.node("GeometryNodeSetShadeSmooth")
    b.feed(smooth.inputs["Geometry"], to_mesh.outputs["Mesh"])

    set_mat = b.node("GeometryNodeSetMaterial")
    b.feed(set_mat.inputs["Geometry"], smooth.outputs["Geometry"])
    b.feed(set_mat.inputs["Material"], I["Material"])
    b.feed(go.inputs["Geometry"], set_mat.outputs["Geometry"])

    b.layout()
    return ng


def get_node_group():
    for ng in bpy.data.node_groups:
        if ng.bl_idname == "GeometryNodeTree" and ng.get(GROUP_TAG) == GROUP_VERSION:
            return ng
    return _build_node_group()


def get_material():
    mat = bpy.data.materials.get(MATERIAL_NAME)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL_NAME)
        mat.diffuse_color = (0.08, 0.05, 0.03, 1.0)
        try:
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get("Principled BSDF")
            if bsdf:
                bsdf.inputs["Base Color"].default_value = (0.08, 0.05, 0.03, 1.0)
                bsdf.inputs["Roughness"].default_value = 0.35
        except (AttributeError, KeyError):
            pass
    return mat


# --------------------------------------------------------------------------
# Modifier helpers
# --------------------------------------------------------------------------

def input_identifiers(ng):
    return {
        item.name: item.identifier
        for item in ng.interface.items_tree
        if item.item_type == "SOCKET" and item.in_out == "INPUT"
    }


def find_proboscis_modifier(obj):
    if obj is None:
        return None
    for mod in obj.modifiers:
        if (mod.type == "NODES" and mod.node_group is not None
                and mod.node_group.get("is_butterfly_proboscis")):
            return mod
    return None


def _input_ref(mod, name):
    """Return (owner, prop_name, data_path) for a modifier input.

    Blender 5.2+ stores inputs as RNA structs (mod.properties.inputs.<id>.value),
    older versions store them as ID properties (mod["<id>"]).
    """
    ident = input_identifiers(mod.node_group).get(name)
    if ident is None:
        return None
    props = getattr(mod, "properties", None)
    inputs = getattr(props, "inputs", None)
    if inputs is not None:
        sock = getattr(inputs, ident, None)
        if sock is not None and hasattr(sock, "value"):
            return sock, "value", sock.path_from_id("value")
    return mod, f'["{ident}"]', f'modifiers["{mod.name}"]["{ident}"]'


def set_input(mod, name, value):
    ref = _input_ref(mod, name)
    if ref is None:
        return
    owner, prop, _ = ref
    if prop == "value":
        owner.value = value
    else:
        owner[prop[2:-2]] = value


def get_input(mod, name):
    ref = _input_ref(mod, name)
    if ref is None:
        return None
    owner, prop, _ = ref
    return owner.value if prop == "value" else owner.get(prop[2:-2])


def keyframe_input(obj, mod, name, value, frame):
    set_input(mod, name, value)
    _, _, data_path = _input_ref(mod, name)
    obj.keyframe_insert(data_path=data_path, frame=frame)


# --------------------------------------------------------------------------
# Operators
# --------------------------------------------------------------------------

class OBJECT_OT_add_butterfly_proboscis(Operator):
    """くるくる巻いた蝶の口吻を追加"""
    bl_idname = "object.add_butterfly_proboscis"
    bl_label = "Butterfly Proboscis"
    bl_options = {"REGISTER", "UNDO"}

    length: FloatProperty(name="Length", default=1.0, min=0.001, unit="LENGTH")
    turns: FloatProperty(name="Turns", default=3.0, min=0.1, max=20.0)
    inner_radius: FloatProperty(name="Inner Radius", default=0.01, min=0.0001,
                                unit="LENGTH")
    base_radius: FloatProperty(name="Base Radius", default=0.01, min=0.0, unit="LENGTH")
    tip_radius: FloatProperty(name="Tip Radius", default=0.004, min=0.0, unit="LENGTH")
    uncoil: FloatProperty(name="Uncoil", default=0.0, min=0.0, max=1.0, subtype="FACTOR")
    parent_to_active: BoolProperty(
        name="Parent to Active",
        description="選択中のアクティブオブジェクト（頭など）の子にする",
        default=True,
    )

    def execute(self, context):
        active = context.active_object
        if active is not None and not active.select_get():
            active = None

        if context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        ng = get_node_group()
        mesh = bpy.data.meshes.new("Proboscis")
        obj = bpy.data.objects.new("Proboscis", mesh)
        context.collection.objects.link(obj)
        obj.location = context.scene.cursor.location

        mod = obj.modifiers.new("Proboscis", "NODES")
        mod.node_group = ng
        set_input(mod, "Length", self.length)
        set_input(mod, "Turns", self.turns)
        set_input(mod, "Inner Radius", self.inner_radius)
        set_input(mod, "Base Radius", self.base_radius)
        set_input(mod, "Tip Radius", self.tip_radius)
        set_input(mod, "Uncoil", self.uncoil)
        set_input(mod, "Material", get_material())

        if self.parent_to_active and active is not None:
            obj.parent = active
            obj.matrix_parent_inverse = active.matrix_world.inverted()

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class OBJECT_OT_proboscis_drink_keys(Operator):
    """口吻を 伸ばす → 水を飲む（探る）→ 巻き戻す キーフレームを打つ"""
    bl_idname = "object.proboscis_drink_keys"
    bl_label = "Add Drinking Animation"
    bl_options = {"REGISTER", "UNDO"}

    start_frame: IntProperty(name="Start Frame", default=1)
    extend_frames: IntProperty(name="Extend", description="伸ばすのにかかるフレーム数",
                               default=24, min=1)
    drink_frames: IntProperty(name="Drink", description="飲んでいる時間（フレーム）",
                              default=60, min=0)
    retract_frames: IntProperty(name="Retract", description="巻き戻すフレーム数",
                                default=36, min=1)
    max_uncoil: FloatProperty(name="Max Uncoil", default=1.0, min=0.0, max=1.0,
                              subtype="FACTOR")
    probe: BoolProperty(name="Probe (Wiggle)",
                        description="飲んでいる間、先端を揺らして探る動きを付ける",
                        default=True)
    wiggle_amount: FloatProperty(name="Wiggle Amount", default=0.6)
    wiggle_cycles: FloatProperty(name="Wiggle Cycles", default=2.0, min=0.0)

    @classmethod
    def poll(cls, context):
        return find_proboscis_modifier(context.active_object) is not None

    def invoke(self, context, event):
        self.start_frame = context.scene.frame_current
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        mod = find_proboscis_modifier(obj)
        f0 = self.start_frame
        f1 = f0 + self.extend_frames
        f2 = f1 + self.drink_frames
        f3 = f2 + self.retract_frames

        keyframe_input(obj, mod, "Uncoil", 0.0, f0)
        keyframe_input(obj, mod, "Uncoil", self.max_uncoil, f1)
        keyframe_input(obj, mod, "Uncoil", self.max_uncoil, f2)
        keyframe_input(obj, mod, "Uncoil", 0.0, f3)

        if self.probe:
            phase0 = get_input(mod, "Wiggle Phase") or 0.0
            keyframe_input(obj, mod, "Wiggle Amount", 0.0, f0)
            keyframe_input(obj, mod, "Wiggle Amount", self.wiggle_amount, f1)
            keyframe_input(obj, mod, "Wiggle Amount", self.wiggle_amount, f2)
            keyframe_input(obj, mod, "Wiggle Amount", 0.0, f3)
            keyframe_input(obj, mod, "Wiggle Phase", phase0, f0)
            keyframe_input(obj, mod, "Wiggle Phase",
                           phase0 + math.tau * self.wiggle_cycles, f3)

        set_input(mod, "Uncoil", 0.0)
        context.scene.frame_set(context.scene.frame_current)
        obj.update_tag()
        self.report({"INFO"}, f"Drinking animation: frames {f0}-{f3}")
        return {"FINISHED"}


# --------------------------------------------------------------------------
# UI
# --------------------------------------------------------------------------

_PANEL_GROUPS = (
    ("Animation", ("Uncoil", "Wiggle Amount", "Wiggle Frequency", "Wiggle Phase")),
    ("Shape", ("Length", "Turns", "Inner Radius", "Straight Base", "Base Angle",
               "Extended Bend", "Uncoil Softness")),
    ("Thickness", ("Base Radius", "Tip Radius", "Resolution", "Profile Resolution",
                   "Material")),
)


class VIEW3D_PT_butterfly_proboscis(Panel):
    bl_label = "Butterfly Proboscis"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Butterfly"

    def draw(self, context):
        layout = self.layout
        layout.operator(OBJECT_OT_add_butterfly_proboscis.bl_idname, icon="ADD",
                        text="Add Proboscis")

        obj = context.active_object
        mod = find_proboscis_modifier(obj)
        if mod is None:
            layout.label(text="口吻オブジェクトを選択してください", icon="INFO")
            return

        for title, names in _PANEL_GROUPS:
            box = layout.box()
            box.label(text=title)
            col = box.column(align=True)
            for name in names:
                ref = _input_ref(mod, name)
                if ref is None:
                    continue
                owner, prop, _ = ref
                if name == "Material" and prop != "value":
                    col.prop_search(owner, prop, bpy.data, "materials", text=name)
                else:
                    col.prop(owner, prop, text=name)
            if title == "Animation":
                box.label(text="値の上で I キー → キーフレーム", icon="KEYFRAME")

        layout.operator(OBJECT_OT_proboscis_drink_keys.bl_idname, icon="MOD_FLUID")


def menu_func(self, context):
    self.layout.operator(OBJECT_OT_add_butterfly_proboscis.bl_idname, icon="CURVE_PATH")


_classes = (
    OBJECT_OT_add_butterfly_proboscis,
    OBJECT_OT_proboscis_drink_keys,
    VIEW3D_PT_butterfly_proboscis,
)


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_mesh_add.append(menu_func)


def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(menu_func)
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
