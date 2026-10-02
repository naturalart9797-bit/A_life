import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)


def _changed(self, context):
    from . import pipeline
    pipeline.schedule(getattr(context.scene.vine_wrap, "target", None))


def _guide_changed(self, context):
    from . import pipeline
    pipeline.schedule(self.target)


def _is_target(_self, obj):
    return obj.type == "MESH" and not obj.get("vine_wrap_source")


# ----------------------------------------------------------------------
class VineStyle(bpy.types.PropertyGroup):
    """見た目のプリセット。ガイドごとに割り当てる"""

    name: StringProperty(name="名前", default="スタイル")
    uid: IntProperty()
    color: FloatVectorProperty(name="表示色", subtype="COLOR", size=3, min=0.0, max=1.0,
                               default=(0.3, 0.9, 0.4))

    radius: FloatProperty(name="太さ", default=0.005, min=0.0001, soft_max=0.05, unit="LENGTH", update=_changed)
    taper: FloatProperty(name="先細り", default=0.6, min=0.0, max=1.0, subtype="FACTOR", update=_changed)
    strands: IntProperty(name="絡むつるの本数", default=1, min=1, max=12, update=_changed,
                         description="1本のガイドに沿って絡み合うつるの本数")
    strand_spread: FloatProperty(name="絡みの幅", default=2.0, min=0.0, soft_max=10.0, update=_changed,
                                 description="ガイドからの広がり（太さに対する倍率）")
    strand_twist: FloatProperty(name="絡みの回転数(/m)", default=6.0, min=0.0, soft_max=50.0, update=_changed)
    strand_radius: FloatProperty(name="絡むつるの太さ比", default=0.7, min=0.05, max=2.0, update=_changed)
    noise_amp: FloatProperty(name="うねり", default=0.0, min=0.0, soft_max=0.1, unit="LENGTH", update=_changed)
    noise_freq: FloatProperty(name="うねりの細かさ", default=8.0, min=0.01, soft_max=100.0, update=_changed)

    tendril_density: FloatProperty(name="巻きひげ(本/m)", default=3.0, min=0.0, soft_max=50.0, update=_changed)
    tendril_size: FloatProperty(name="巻きひげの長さ", default=0.06, min=0.001, soft_max=0.5, unit="LENGTH",
                                update=_changed)

    use_leaves: BoolProperty(name="葉", default=True, update=_changed)
    leaf_density: FloatProperty(name="葉の密度(枚/m)", default=40.0, min=0.0, soft_max=300.0, update=_changed)
    leaf_size: FloatProperty(name="葉の大きさ", default=0.045, min=0.001, soft_max=0.3, unit="LENGTH",
                             update=_changed)
    leaf_size_var: FloatProperty(name="大きさのばらつき", default=0.35, min=0.0, max=1.0, subtype="FACTOR",
                                 update=_changed)
    leaf_width: FloatProperty(name="葉の幅", default=0.55, min=0.05, max=2.0, update=_changed)
    leaf_tilt: FloatProperty(name="葉の起き上がり", default=0.25, min=-1.0, max=2.0, update=_changed)
    leaf_curl: FloatProperty(name="葉の反り", default=0.2, min=-1.0, max=1.0, update=_changed)

    stem_color: FloatVectorProperty(name="茎の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.12, 0.18, 0.06), update=_changed)
    leaf_color: FloatVectorProperty(name="葉の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.12, 0.35, 0.08), update=_changed)
    color_var: FloatProperty(name="色のばらつき", default=0.5, min=0.0, max=2.0, update=_changed)


class GuideSettings(bpy.types.PropertyGroup):
    """カーブオブジェクトに付く、ガイドとしての設定"""

    target: PointerProperty(name="対象", type=bpy.types.Object,
                            description="このガイドが絡む対象オブジェクト")
    style_uid: IntProperty(name="スタイル", default=0, update=_guide_changed)
    snap: BoolProperty(name="表面に吸着", default=True, update=_guide_changed,
                       description="オン: 対象の表面に沿う / オフ: 描いた位置のまま空間に浮かぶ")
    radius: FloatProperty(name="太さ倍率", default=1.0, min=0.0, soft_max=5.0, update=_guide_changed)
    leaves: FloatProperty(name="葉の量倍率", default=1.0, min=0.0, soft_max=5.0, update=_guide_changed)
    enabled: BoolProperty(name="使用", default=True, update=_guide_changed)
    auto: BoolProperty(name="自動生成", default=False,
                       description="ペイントからの自動生成で作られたガイド（再生成で置き換わる）")


GEN_AXES = [
    ("WORLD_Z", "ワールドZ", "上下方向に巻き付く・登る"),
    ("LOCAL_X", "ローカルX", "対象のローカルX軸の周りに巻き付く"),
    ("LOCAL_Y", "ローカルY", "対象のローカルY軸の周りに巻き付く"),
    ("LOCAL_Z", "ローカルZ", "対象のローカルZ軸の周りに巻き付く"),
]


class VineWrapSettings(bpy.types.PropertyGroup):
    # --- target -------------------------------------------------------
    target: PointerProperty(name="対象", type=bpy.types.Object, poll=_is_target,
                            description="つるを絡ませるメッシュオブジェクト")
    auto_scale: BoolProperty(
        name="サイズ自動スケール", default=True, update=_changed,
        description="長さの値を「最大寸法1.7m」の物体を基準とし、対象の大きさに合わせて拡大縮小する")
    rest_pose: BoolProperty(name="レストポーズで処理", default=True,
                            description="アーマチュアをレストポーズにしてから生成・バインドする（推奨）")

    # --- build --------------------------------------------------------
    auto_update: BoolProperty(name="自動更新", default=True,
                              description="ガイドや設定を変えたら自動でつるを作り直す")
    bind_mode: EnumProperty(
        name="追従方法",
        items=[
            ("ARMATURE", "アーマチュア", "対象のボーンウェイトを転写してArmatureモディファイアで変形"),
            ("SURFACE", "サーフェス変形", "Surface Deformで対象の表面に貼り付ける（シェイプキー/Alembic向け）"),
            ("NONE", "なし", "親子付けのみ"),
        ],
        default="ARMATURE", update=_changed)
    ring_res: IntProperty(name="断面分割数", default=6, min=3, max=24, update=_changed)
    step_length: FloatProperty(name="セグメント長", default=0.01, min=0.001, soft_max=0.05, unit="LENGTH",
                               update=_changed)
    surface_offset: FloatProperty(name="表面からの距離", default=0.002, min=0.0, soft_max=0.05, unit="LENGTH",
                                  update=_changed)
    seed: IntProperty(name="シード", default=1, min=0, update=_changed)

    # --- styles & guides ---------------------------------------------
    styles: CollectionProperty(type=VineStyle)
    active_style_index: IntProperty(default=0)
    next_uid: IntProperty(default=1)
    active_guide_index: IntProperty(default=-1, update=lambda self, ctx: _select_listed_guide(self, ctx))
    guide_filter: StringProperty(name="名前で絞り込み", default="", options={"TEXTEDIT_UPDATE"})

    # --- tools --------------------------------------------------------
    point_spacing: FloatProperty(name="点の間隔", default=0.03, min=0.002, soft_max=0.3, unit="LENGTH",
                                 description="描いたガイドの制御点の間隔")
    soft_range: IntProperty(name="なめらか移動(点数)", default=2, min=0, max=30,
                            description="点を動かしたとき、前後の何点まで一緒に動かすか")
    guide_hover: FloatProperty(name="ガイドの浮き", default=0.008, min=0.0, soft_max=0.05, unit="LENGTH",
                               description="吸着ガイドを表面からどれだけ浮かせて表示するか")
    show_points: BoolProperty(name="制御点を表示", default=True)
    brush_radius: IntProperty(name="半径(px)", default=60, min=5, max=500, subtype="PIXEL")
    brush_strength: FloatProperty(name="強さ", default=0.5, min=0.0, max=1.0, subtype="FACTOR")
    show_paint: BoolProperty(name="ペイントを表示", default=True,
                             description="密度ペイントツールの使用中に、塗った範囲を表示する")
    show_paint_always: BoolProperty(name="常にペイントを表示", default=False,
                                    description="ツールに関係なく、塗った範囲を常に表示する")

    # --- auto generation from paint -----------------------------------
    paint_group: StringProperty(name="ペイント", default="VineDensity",
                                description="生成範囲・密度を表す頂点グループ（ペイントツールで描く）")
    gen_density: FloatProperty(name="密度(本/㎡)", default=60.0, min=0.0, soft_max=1000.0,
                               description="ウェイト1の面積1㎡あたりに生えるつるの本数")
    gen_max: IntProperty(name="最大本数", default=200, min=1, max=5000)
    gen_length: FloatProperty(name="長さ", default=0.5, min=0.01, soft_max=5.0, unit="LENGTH")
    gen_length_var: FloatProperty(name="長さのばらつき", default=0.5, min=0.0, max=1.0, subtype="FACTOR")
    gen_spacing: FloatProperty(name="つる間隔", default=0.02, min=0.001, soft_max=0.2, unit="LENGTH",
                               description="つる同士がこれ以上近づかない距離")
    gen_step: FloatProperty(name="ステップ長", default=0.01, min=0.001, soft_max=0.05, unit="LENGTH")
    gen_axis: EnumProperty(name="巻き付く軸", items=GEN_AXES, default="WORLD_Z")
    gen_wrap: FloatProperty(name="巻き付き", default=0.7, min=0.0, max=3.0,
                            description="軸の周りをぐるりと巻き付く傾向")
    gen_climb: FloatProperty(name="登る/垂れる", default=0.25, min=-2.0, max=2.0,
                             description="正: 軸の＋方向へ登る / 負: 垂れ下がる")
    gen_spiral_bias: FloatProperty(name="巻き方向の偏り", default=0.0, min=-1.0, max=1.0,
                                   description="-1=全て逆巻き, 0=ランダム, 1=全て同方向")
    gen_persistence: FloatProperty(name="直進性", default=1.0, min=0.0, max=5.0)
    gen_noise: FloatProperty(name="うねり", default=0.45, min=0.0, max=3.0)
    gen_branch: FloatProperty(name="分岐確率", default=0.03, min=0.0, max=0.5)
    gen_depth: IntProperty(name="分岐の深さ", default=2, min=0, max=5)
    gen_branch_radius: FloatProperty(name="枝の太さ比", default=0.7, min=0.1, max=1.0)
    gen_threshold: FloatProperty(name="しきい値", default=0.05, min=0.0, max=1.0, subtype="FACTOR",
                                 description="このウェイト未満の場所には伸びない")
    gen_avoid: BoolProperty(name="既存のガイドを避ける", default=True)
    gen_replace: BoolProperty(name="前回の自動生成を置き換え", default=True)
    gen_ctrl_spacing: FloatProperty(name="制御点の間隔", default=0.04, min=0.005, soft_max=0.3, unit="LENGTH",
                                    description="自動生成したガイドの制御点の間隔（大きいほど編集しやすい）")


def _select_listed_guide(P, context):
    objs = bpy.data.objects
    if not (0 <= P.active_guide_index < len(objs)):
        return
    obj = objs[P.active_guide_index]
    if obj.type != "CURVE" or obj.vine_guide.target is None:
        return
    if obj.name not in context.view_layer.objects:
        return
    if context.mode != "OBJECT":
        return
    for o in context.selected_objects:
        o.select_set(False)
    try:
        obj.hide_set(False)
        obj.select_set(True)
    except RuntimeError:
        return
    context.view_layer.objects.active = obj


classes = (VineStyle, GuideSettings, VineWrapSettings)
