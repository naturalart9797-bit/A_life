import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty


def _is_target(_self, obj):
    return obj.type == "MESH" and not obj.get("slime_net_source")


def _growth_changed(self, context):
    from . import pipeline
    t = self.target
    if t is None:
        return
    for o in pipeline.network_objects(t):
        node = pipeline.growth_node(o)
        if node is not None:
            node.outputs[0].default_value = self.growth


def _reach_changed(self, context):
    from . import pipeline
    pipeline.sync_display(self)


class SlimeNetSettings(bpy.types.PropertyGroup):
    target: PointerProperty(name="対象", type=bpy.types.Object, poll=_is_target,
                            description="ネットワークを広げるメッシュオブジェクト")
    auto_scale: BoolProperty(name="サイズ自動スケール", default=True,
                             description="長さの値を「最大寸法1.7m」の物体を基準とし、対象の大きさに合わせて拡大縮小する")
    rest_pose: BoolProperty(name="レストポーズで処理", default=True)
    seed: IntProperty(name="シード", default=1, min=0)

    default_reach: FloatProperty(name="新しい起点の範囲", default=0.35, min=0.01, soft_max=3.0, unit="LENGTH",
                                 description="新しく置く起点の広がる範囲（表面に沿った距離）")

    spacing: FloatProperty(name="網の細かさ", default=0.012, min=0.002, soft_max=0.1, unit="LENGTH",
                           description="ネットワークのもとになる点の間隔。小さいほど細かい網になる（重くなる）")
    food_count: IntProperty(name="枝先の数", default=40, min=2, max=2000,
                            description="管が向かう『餌』の数。多いほど枝分かれが増える")
    front_ratio: FloatProperty(name="前線の割合", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                               description="餌のうち範囲の外周付近に置く割合。高いと扇状に広がる前線ができる")
    loopiness: FloatProperty(name="網目の多さ", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                             description="0: 木のように枝分かれ / 1: ループの多い網目状")
    keep_decades: FloatProperty(name="細い管を残す", default=2.0, min=0.3, max=5.0,
                                description="どこまで細い管を残すか。大きいほど細い毛細管まで残る")
    origin_bias: FloatProperty(name="起点からの流れ", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                               description="起点から流れる割合。高いほど起点から放射状に太い管が出る")
    iterations: IntProperty(name="計算回数", default=80, min=5, max=1000)

    r_min: FloatProperty(name="最小の太さ", default=0.0008, min=0.00005, soft_max=0.01, unit="LENGTH")
    r_max: FloatProperty(name="最大の太さ", default=0.006, min=0.0002, soft_max=0.05, unit="LENGTH")
    surface_offset: FloatProperty(name="表面からの距離", default=0.0005, min=0.0, soft_max=0.02, unit="LENGTH")
    ring_res: IntProperty(name="断面分割数", default=6, min=3, max=16)

    color_thin: FloatVectorProperty(name="細い管の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.85, 0.55, 0.08))
    color_thick: FloatVectorProperty(name="太い管の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=(0.95, 0.75, 0.12))
    roughness: FloatProperty(name="粗さ", default=0.3, min=0.0, max=1.0, subtype="FACTOR")
    subsurface: FloatProperty(name="透け感(SSS)", default=0.3, min=0.0, max=1.0, subtype="FACTOR")

    growth: FloatProperty(name="成長", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_growth_changed,
                          description="0: 起点だけ → 1: 範囲いっぱい。キーフレームを打つなら生成物の「成長」モディファイアに")
    last_message: bpy.props.StringProperty(default="")
    last_ok: BoolProperty(default=True)
    bind_mode: EnumProperty(
        name="追従方法",
        items=[("ARMATURE", "アーマチュア", "対象のボーンウェイトを転写してArmatureモディファイアで変形"),
               ("SURFACE", "サーフェス変形", "Surface Deformで対象の表面に貼り付ける"),
               ("NONE", "なし", "親子付けのみ")],
        default="ARMATURE")


classes = (SlimeNetSettings,)

REACH = FloatProperty(name="範囲", default=0.0, min=0.0, soft_max=5.0, unit="LENGTH", update=_reach_changed,
                      description="起点から表面に沿って広がる距離（ワールド単位）")
