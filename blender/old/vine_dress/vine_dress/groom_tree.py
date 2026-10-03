"""Vine Groom node tree (Yeti-like graph).

Guide groups enter the graph through "ガイドグループ" nodes, flow through
attribute / geometry nodes and end in the "出力" node, which builds the vine
mesh. Every node carries a list of Batches (guide splines + attributes), so
different branches can give different groups a different look.
"""

import random
import time
import traceback

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty
from bpy.types import Node, NodeSocket, NodeTree

from . import guides
from .build import Attrs, Batch, read_group, scatter

TREE_ID = "VineGroomTreeType"
SOCKET_ID = "VineGuidesSocket"


# ----------------------------------------------------------------------
# Auto rebuild (debounced)
# ----------------------------------------------------------------------
_pending = set()
_busy = [False]


def schedule(tree):
    if tree is None or _busy[0] or not getattr(tree, "auto_update", False) or tree.body is None:
        return
    _pending.add(tree.name)
    if not bpy.app.timers.is_registered(_run_pending):
        bpy.app.timers.register(_run_pending, first_interval=0.4)


def schedule_for_body(body):
    if body is None:
        return
    for tree in bpy.data.node_groups:
        if tree.bl_idname == TREE_ID and tree.body == body:
            schedule(tree)


def _run_pending():
    names = list(_pending)
    _pending.clear()
    for name in names:
        tree = bpy.data.node_groups.get(name)
        if tree is not None:
            run_build(bpy.context, tree, lambda kind, msg: print("[Vine Dress]", msg))
    return None


def run_build(context, tree, report):
    from . import pipeline

    if tree.body is None:
        report({"ERROR"}, "グルームツリーに人物が設定されていません")
        return None
    _busy[0] = True
    t0 = time.time()
    try:
        obj, info = pipeline.build_vines(report, context, context.scene.vine_dress, tree.body, tree)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        report({"ERROR"}, "生成に失敗しました: %s" % exc)
        return None
    finally:
        _busy[0] = False
    if obj is None:
        report({"ERROR"}, info)
        return None
    msg = pipeline.format_stats(info, time.time() - t0)
    report({"WARNING"} if info.get("bind_failed") else {"INFO"},
           msg + (" ※サーフェス変形のバインドに失敗" if info.get("bind_failed") else ""))
    return obj


def _upd(self, _context):
    schedule(self.id_data)


# ----------------------------------------------------------------------
# Tree & socket
# ----------------------------------------------------------------------
class VineGroomTree(NodeTree):
    """つる植物のグルーミンググラフ"""

    bl_idname = TREE_ID
    bl_label = "Vine Groom"
    bl_icon = "OUTLINER_OB_CURVES"

    body: PointerProperty(name="人物", type=bpy.types.Object,
                          poll=lambda self, obj: obj.type == "MESH")
    auto_update: BoolProperty(name="自動更新", default=False,
                              description="ノードやガイドを変更したら自動でつるを作り直す",
                              update=lambda self, ctx: schedule(self))

    def update(self):
        schedule(self)


class VineGuidesSocket(NodeSocket):
    bl_idname = SOCKET_ID
    bl_label = "ガイド"

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return (0.35, 0.95, 0.45, 1.0)


def new_input(node, multi=False):
    if multi:
        try:
            sock = node.inputs.new(SOCKET_ID, "ガイド", use_multi_input=True)
        except TypeError:  # Blender < 4.2: no multi-input flag for Python sockets
            sock = node.inputs.new(SOCKET_ID, "ガイド")
        sock.link_limit = 4095
        return sock
    return node.inputs.new(SOCKET_ID, "ガイド")


class _VineNode:
    bl_width_default = 180

    @classmethod
    def poll(cls, ntree):
        return ntree.bl_idname == TREE_ID

    def _io(self, inputs=1, multi=False, output=True):
        for _ in range(inputs):
            new_input(self, multi)
        if output:
            self.outputs.new(SOCKET_ID, "ガイド")

    def init(self, context):
        self._io()

    def process(self, inputs, ctx):
        return [b for lst in inputs for b in lst]


def _attr_node(label, idname, fields):
    """Helper for nodes that just set attributes on every batch."""
    def process(self, inputs, ctx):
        vals = {f: getattr(self, f) for f in fields}
        return [b.with_attrs(**vals) for lst in inputs for b in lst]
    return process


# ----------------------------------------------------------------------
# Nodes
# ----------------------------------------------------------------------
def _poll_group(self, obj):
    tree = self.id_data
    return guides.is_guide(obj) and (tree.body is None or obj.vine_guide.body == tree.body)


def _group_changed(self, ctx):
    if self.group is not None:
        self.use_custom_color = True
        self.color = [c * 0.5 for c in self.group.vine_guide.color]
        self.label = self.group.name
    _upd(self, ctx)


class VineNodeGroupInput(_VineNode, Node):
    """ガイドグループをグラフに入れる"""

    bl_idname = "VineNodeGroupInput"
    bl_label = "ガイドグループ"
    bl_icon = "OUTLINER_OB_CURVE"

    group: PointerProperty(name="グループ", type=bpy.types.Object, poll=_poll_group, update=_group_changed)

    def init(self, context):
        self.outputs.new(SOCKET_ID, "ガイド")

    def draw_buttons(self, context, layout):
        layout.prop(self, "group", text="")
        g = self.group
        if g is not None:
            row = layout.row()
            row.prop(g.vine_guide, "color", text="")
            row.label(text="%d 本" % len(g.data.splines),
                      icon="MOD_SHRINKWRAP" if g.vine_guide.kind == guides.KIND_BODY else "MOD_CLOTH")

    def process(self, inputs, ctx):
        if self.group is None:
            return []
        return [Batch(read_group(self.group, ctx.step), Attrs(), self.group.name)]


class VineNodeMerge(_VineNode, Node):
    """複数の流れをまとめる"""

    bl_idname = "VineNodeMerge"
    bl_label = "マージ"
    bl_icon = "SELECT_EXTEND"

    def init(self, context):
        self._io(multi=True)


class VineNodeScatter(_VineNode, Node):
    """ガイドの周りに子ガイドを散布して本数を増やす"""

    bl_idname = "VineNodeScatter"
    bl_label = "散布"
    bl_icon = "STICKY_UVS_DISABLE"

    count: IntProperty(name="子の数", default=2, min=0, max=50, update=_upd)
    radius: FloatProperty(name="散布半径", default=0.03, min=0.0, soft_max=0.3, unit="LENGTH", update=_upd)
    length_var: FloatProperty(name="長さのばらつき", default=0.4, min=0.0, max=1.0, subtype="FACTOR",
                              update=_upd)
    wobble: FloatProperty(name="ゆらぎ", default=0.4, min=0.0, max=2.0, update=_upd)
    seed: IntProperty(name="シード", default=0, update=_upd)

    def draw_buttons(self, context, layout):
        for f in ("count", "radius", "length_var", "wobble", "seed"):
            layout.prop(self, f)

    def process(self, inputs, ctx):
        rng = random.Random(self.seed * 7907 + 11)
        out = []
        for lst in inputs:
            for b in lst:
                need = any(s.kind == guides.KIND_SKIRT for s in b.splines)
                spl = scatter(b.splines, ctx.sampler, ctx.field if need else None, self.count,
                              self.radius, self.length_var, self.wobble, rng, ctx.scale)
                out.append(Batch(spl, b.attrs, b.label))
        return out


class VineNodeThickness(_VineNode, Node):
    """つるの太さ"""

    bl_idname = "VineNodeThickness"
    bl_label = "太さ"
    bl_icon = "MOD_THICKNESS"

    radius: FloatProperty(name="太さ", default=0.005, min=0.0001, soft_max=0.05, unit="LENGTH", update=_upd)
    radius_mult: FloatProperty(name="倍率", default=1.0, min=0.0, soft_max=5.0, update=_upd)
    taper: FloatProperty(name="先細り", default=0.0, min=0.0, max=1.0, subtype="FACTOR", update=_upd)
    process = _attr_node("太さ", "thick", ("radius", "radius_mult", "taper"))

    def draw_buttons(self, context, layout):
        for f in ("radius", "radius_mult", "taper"):
            layout.prop(self, f)


class VineNodeStrands(_VineNode, Node):
    """1本のガイドに複数のつるを絡ませる"""

    bl_idname = "VineNodeStrands"
    bl_label = "絡み"
    bl_icon = "FORCE_VORTEX"

    strands: IntProperty(name="本数", default=3, min=1, max=12, update=_upd)
    strand_spread: FloatProperty(name="幅(太さ比)", default=2.0, min=0.0, soft_max=10.0, update=_upd)
    strand_twist: FloatProperty(name="回転数(/m)", default=6.0, min=0.0, soft_max=50.0, update=_upd)
    strand_radius: FloatProperty(name="太さ比", default=0.7, min=0.05, max=2.0, update=_upd)
    process = _attr_node("絡み", "strands", ("strands", "strand_spread", "strand_twist", "strand_radius"))

    def draw_buttons(self, context, layout):
        for f in ("strands", "strand_spread", "strand_twist", "strand_radius"):
            layout.prop(self, f)


class VineNodeTendrils(_VineNode, Node):
    """螺旋状の巻きひげ"""

    bl_idname = "VineNodeTendrils"
    bl_label = "巻きひげ"
    bl_icon = "FORCE_MAGNETIC"

    tendril_density: FloatProperty(name="密度(本/m)", default=3.0, min=0.0, soft_max=50.0, update=_upd)
    tendril_size: FloatProperty(name="長さ", default=0.06, min=0.001, soft_max=0.5, unit="LENGTH",
                                update=_upd)
    process = _attr_node("巻きひげ", "tendrils", ("tendril_density", "tendril_size"))

    def draw_buttons(self, context, layout):
        layout.prop(self, "tendril_density")
        layout.prop(self, "tendril_size")


class VineNodeLeaves(_VineNode, Node):
    """葉"""

    bl_idname = "VineNodeLeaves"
    bl_label = "葉"
    bl_icon = "OUTLINER_DATA_POINTCLOUD"

    use_leaves: BoolProperty(name="葉を付ける", default=True, update=_upd)
    leaf_density: FloatProperty(name="密度(枚/m)", default=40.0, min=0.0, soft_max=300.0, update=_upd)
    leaf_size: FloatProperty(name="大きさ", default=0.045, min=0.001, soft_max=0.3, unit="LENGTH", update=_upd)
    leaf_size_var: FloatProperty(name="ばらつき", default=0.35, min=0.0, max=1.0, subtype="FACTOR", update=_upd)
    leaf_width: FloatProperty(name="幅", default=0.55, min=0.05, max=2.0, update=_upd)
    leaf_tilt: FloatProperty(name="起き上がり", default=0.25, min=-1.0, max=2.0, update=_upd)
    leaf_curl: FloatProperty(name="反り", default=0.2, min=-1.0, max=1.0, update=_upd)
    _F = ("use_leaves", "leaf_density", "leaf_size", "leaf_size_var", "leaf_width", "leaf_tilt", "leaf_curl")
    process = _attr_node("葉", "leaves", _F)

    def draw_buttons(self, context, layout):
        for f in self._F:
            layout.prop(self, f)


class VineNodeNoise(_VineNode, Node):
    """つるをノイズでうねらせる"""

    bl_idname = "VineNodeNoise"
    bl_label = "ノイズ"
    bl_icon = "MOD_NOISE"

    noise_amp: FloatProperty(name="強さ", default=0.01, min=0.0, soft_max=0.2, unit="LENGTH", update=_upd)
    noise_freq: FloatProperty(name="細かさ", default=8.0, min=0.01, soft_max=100.0, update=_upd)
    noise_seed: IntProperty(name="シード", default=0, update=_upd)
    process = _attr_node("ノイズ", "noise", ("noise_amp", "noise_freq", "noise_seed"))

    def draw_buttons(self, context, layout):
        for f in ("noise_amp", "noise_freq", "noise_seed"):
            layout.prop(self, f)


class VineNodeColor(_VineNode, Node):
    """茎と葉の色"""

    bl_idname = "VineNodeColor"
    bl_label = "色"
    bl_icon = "COLOR"

    stem_color: FloatVectorProperty(name="茎", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.12, 0.18, 0.06), update=_upd)
    leaf_color: FloatVectorProperty(name="葉", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.12, 0.35, 0.08), update=_upd)
    color_var: FloatProperty(name="ばらつき", default=0.5, min=0.0, max=2.0, update=_upd)

    def process(self, inputs, ctx):
        vals = {"stem_color": tuple(self.stem_color), "leaf_color": tuple(self.leaf_color),
                "color_var": self.color_var}
        return [b.with_attrs(**vals) for lst in inputs for b in lst]

    def draw_buttons(self, context, layout):
        for f in ("stem_color", "leaf_color", "color_var"):
            layout.prop(self, f)


class VineNodeSnap(_VineNode, Node):
    """体表面ガイドを体に吸着させるか"""

    bl_idname = "VineNodeSnap"
    bl_label = "体への吸着"
    bl_icon = "SNAP_ON"

    snap: BoolProperty(name="吸着する", default=True, update=_upd)
    surface_offset: FloatProperty(name="肌からの距離", default=0.002, min=0.0, soft_max=0.05,
                                  unit="LENGTH", update=_upd)
    process = _attr_node("吸着", "snap", ("snap", "surface_offset"))

    def draw_buttons(self, context, layout):
        layout.prop(self, "snap")
        layout.prop(self, "surface_offset")


class VineNodeOutput(_VineNode, Node):
    """つるメッシュを生成する"""

    bl_idname = "VineNodeOutput"
    bl_label = "出力"
    bl_icon = "OUTPUT"
    bl_width_default = 220

    step_length: FloatProperty(name="セグメント長", default=0.01, min=0.001, soft_max=0.05, unit="LENGTH",
                               update=_upd)
    ring_res: IntProperty(name="断面分割数", default=6, min=3, max=24, update=_upd)
    bind_mode: EnumProperty(
        name="追従方法",
        items=[
            ("ARMATURE", "アーマチュア", "人物のボーンウェイトを転写してArmatureモディファイアで変形（推奨）"),
            ("SURFACE", "サーフェス変形", "Surface Deformで人物メッシュに貼り付ける（シェイプキー/Alembic向け）"),
            ("NONE", "なし", "親子付けのみ"),
        ],
        default="ARMATURE", update=_upd)
    skirt_stiffness: FloatProperty(
        name="腰への固定度", default=0.85, min=0.0, max=1.0, subtype="FACTOR", update=_upd,
        description="スカートの裾がどれだけ腰の動きに従うか。0=近くの脚に追従, 1=腰に固定")
    use_sway: BoolProperty(name="水中のゆらめき", default=True, update=_upd)
    sway_strength: FloatProperty(name="強さ", default=0.02, min=0.0, soft_max=0.5, unit="LENGTH", update=_upd)
    sway_scale: FloatProperty(name="大きさ", default=0.35, min=0.01, soft_max=5.0, update=_upd)
    sway_speed: FloatProperty(name="速度", default=0.01, min=0.0, soft_max=0.2, update=_upd)

    def init(self, context):
        new_input(self, True)

    def draw_buttons(self, context, layout):
        tree = self.id_data
        row = layout.row(align=True)
        row.scale_y = 1.4
        op = row.operator("vine_dress.build", icon="PLAY")
        op.tree_name = tree.name
        layout.prop(tree, "auto_update", icon="FILE_REFRESH")
        layout.prop(self, "bind_mode", text="")
        col = layout.column(align=True)
        col.prop(self, "step_length")
        col.prop(self, "ring_res")
        col.prop(self, "skirt_stiffness")
        layout.prop(self, "use_sway")
        if self.use_sway:
            col = layout.column(align=True)
            col.prop(self, "sway_strength")
            col.prop(self, "sway_scale")
            col.prop(self, "sway_speed")

    def process(self, inputs, ctx):
        return [b for lst in inputs for b in lst]


NODE_CLASSES = (
    VineNodeGroupInput,
    VineNodeMerge,
    VineNodeScatter,
    VineNodeThickness,
    VineNodeStrands,
    VineNodeTendrils,
    VineNodeLeaves,
    VineNodeNoise,
    VineNodeColor,
    VineNodeSnap,
    VineNodeOutput,
)

MENU = (
    ("入力", ("VineNodeGroupInput",)),
    ("形状", ("VineNodeScatter", "VineNodeStrands", "VineNodeNoise", "VineNodeSnap")),
    ("見た目", ("VineNodeThickness", "VineNodeTendrils", "VineNodeLeaves", "VineNodeColor")),
    ("その他", ("VineNodeMerge", "VineNodeOutput")),
)


# ----------------------------------------------------------------------
# Evaluation
# ----------------------------------------------------------------------
def output_node(tree):
    for n in tree.nodes:
        if n.bl_idname == "VineNodeOutput":
            return n
    return None


def _socket_links(tree, sock):
    links = [l for l in tree.links if l.to_socket == sock and l.is_valid and not getattr(l, "is_muted", False)]
    links.sort(key=lambda l: getattr(l, "multi_input_sort_id", 0))
    return links


def evaluate(tree, ctx):
    out = output_node(tree)
    if out is None:
        return []
    cache = {}
    stack = set()

    def eval_socket(sock):
        res = []
        for link in _socket_links(tree, sock):
            res += eval_node(link.from_node)
        return res

    def eval_node(node):
        if node.name in cache:
            return cache[node.name]
        if node.name in stack:
            return []  # cycle
        stack.add(node.name)
        inputs = [eval_socket(s) for s in node.inputs]
        if node.bl_idname == "NodeReroute" or getattr(node, "mute", False) or not hasattr(node, "process"):
            res = [b for lst in inputs for b in lst]
        else:
            res = node.process(inputs, ctx)
        stack.discard(node.name)
        cache[node.name] = res
        return res

    return eval_socket(out.inputs[0])


# ----------------------------------------------------------------------
# Tree helpers
# ----------------------------------------------------------------------
def trees_of(body):
    return [t for t in bpy.data.node_groups if t.bl_idname == TREE_ID and t.body == body]


def ensure_tree(context, body):
    P = context.scene.vine_dress
    tree = P.groom_tree
    if tree is None or tree.bl_idname != TREE_ID or (tree.body is not None and tree.body != body):
        found = trees_of(body)
        tree = found[0] if found else None
    if tree is None:
        tree = bpy.data.node_groups.new(body.name + "_Groom", TREE_ID)
        tree.body = body
        out = tree.nodes.new("VineNodeOutput")
        out.location = (500, 0)
    if tree.body is None:
        tree.body = body
    P.groom_tree = tree
    return tree


def group_in_tree(tree, obj):
    return any(n.bl_idname == "VineNodeGroupInput" and n.group == obj for n in tree.nodes)


def tree_add_group(tree, obj):
    """Add an input chain for a group (input -> [thickness] -> leaves -> output)."""
    if group_in_tree(tree, obj):
        return
    _busy[0] = True
    try:
        out = output_node(tree)
        if out is None:
            out = tree.nodes.new("VineNodeOutput")
            out.location = (500, 0)
        ys = [n.location.y for n in tree.nodes if n.bl_idname == "VineNodeGroupInput"]
        y = (min(ys) - 300) if ys else 150
        inp = tree.nodes.new("VineNodeGroupInput")
        inp.location = (-500, y)
        inp.group = obj
        last = inp
        x = -250
        if obj.vine_guide.kind == guides.KIND_SKIRT:
            th = tree.nodes.new("VineNodeThickness")
            th.location = (x, y)
            th.radius_mult = 1.2
            tree.links.new(last.outputs[0], th.inputs[0])
            last = th
            x += 220
        tn = tree.nodes.new("VineNodeTendrils")
        tn.location = (x, y)
        tree.links.new(last.outputs[0], tn.inputs[0])
        last = tn
        x += 220
        lf = tree.nodes.new("VineNodeLeaves")
        lf.location = (x, y)
        tree.links.new(last.outputs[0], lf.inputs[0])
        tree.links.new(lf.outputs[0], out.inputs[0])
        for n in tree.nodes:
            n.select = False
    finally:
        _busy[0] = False


def remove_group_nodes(obj):
    for tree in bpy.data.node_groups:
        if tree.bl_idname != TREE_ID:
            continue
        for n in [n for n in tree.nodes if n.bl_idname == "VineNodeGroupInput" and n.group == obj]:
            tree.nodes.remove(n)


# ----------------------------------------------------------------------
# Add menu & sidebar in the node editor
# ----------------------------------------------------------------------
class VINEDRESS_MT_add_nodes(bpy.types.Menu):
    bl_idname = "VINEDRESS_MT_add_nodes"
    bl_label = "Vine Groom"

    def draw(self, context):
        layout = self.layout
        for i, (title, ids) in enumerate(MENU):
            if i:
                layout.separator()
            layout.label(text=title)
            for idname in ids:
                cls = getattr(bpy.types, idname)
                op = layout.operator("node.add_node", text=cls.bl_label, icon=cls.bl_icon)
                op.type = idname
                op.use_transform = True


def draw_add_menu(self, context):
    if getattr(context.space_data, "tree_type", "") == TREE_ID:
        self.layout.menu(VINEDRESS_MT_add_nodes.bl_idname, icon="OUTLINER_OB_CURVES")


class VINEDRESS_PT_node_sidebar(bpy.types.Panel):
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Vine Dress"
    bl_label = "Vine Groom"

    @classmethod
    def poll(cls, context):
        return getattr(context.space_data, "tree_type", "") == TREE_ID and context.space_data.node_tree

    def draw(self, context):
        tree = context.space_data.node_tree
        layout = self.layout
        layout.prop(tree, "body")
        layout.prop(tree, "auto_update", icon="FILE_REFRESH")
        op = layout.operator("vine_dress.build", icon="PLAY")
        op.tree_name = tree.name
        layout.operator("vine_dress.tree_sync_groups", icon="ADD")


classes = (VineGroomTree, VineGuidesSocket) + NODE_CLASSES + (VINEDRESS_MT_add_nodes, VINEDRESS_PT_node_sidebar)
