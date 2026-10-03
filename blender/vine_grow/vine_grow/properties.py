import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty


def _is_target(_self, obj):
    return obj.type == "MESH" and not obj.get("vine_grow_source")


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


class VineGrowSettings(bpy.types.PropertyGroup):
    target: PointerProperty(name="対象", type=bpy.types.Object, poll=_is_target,
                            description="つるを生やすメッシュオブジェクト")
    auto_scale: BoolProperty(name="サイズ自動スケール", default=True,
                             description="長さの値を「最大寸法1.7m」の物体を基準とし、対象の大きさに合わせて拡大縮小する")
    rest_pose: BoolProperty(name="レストポーズで処理", default=True)
    seed: IntProperty(name="シード", default=1, min=0)
    default_reach: FloatProperty(name="新しい起点の範囲", default=0.15, min=0.01, soft_max=3.0, unit="LENGTH",
                                 description="新しく置く起点からつるが広がる範囲（体に沿った距離）")

    mode: EnumProperty(
        name="生え方", default="DENSITY",
        items=[("DENSITY", "密度（起点の周りに茂る）", "起点の周りほど密に、細いつるが方向なく絡まり合って茂る"),
               ("ROUTE", "経路（起点を通過）", "起点を順番に通る経路に沿って、つるが絡み合いながら伸びる"),
               ("RADIAL", "放射（起点から広がる）", "起点から周囲に枝分かれしながら広がる")])
    default_width: FloatProperty(name="新しい経由点の幅", default=0.05, min=0.002, soft_max=0.5, unit="LENGTH",
                                 description="経路モードで新しく置く経由点の、つるの束の幅（半径）")
    strand_count: IntProperty(name="つるの本数", default=7, min=1, max=200,
                              description="経路に沿って伸びるつるの本数")
    twist: FloatProperty(name="ねじれ(回/m)", default=2.0, min=0.0, soft_max=20.0,
                         description="つるが経路の周りを巻く回数（1mあたり）。細い腕などではぐるぐる巻き付く")
    wrap_threshold: FloatProperty(
        name="巻き付く細さ", default=1.0, min=0.1, max=4.0,
        description="束の幅が体の部分の半径のこの倍率以上になる所（腕・首など細い所）で、ぐるりと巻き付く。"
                    "小さいほど太い所でも巻き付く")
    counter_twist: BoolProperty(name="逆巻きを混ぜる", default=True,
                                description="半分のつるを逆向きに巻いて、編み込んだように交差させる")
    strand_sync: FloatProperty(name="始まりと終わりの揃い", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                               description="1: 全部のつるが最初と最後の経由点で揃う / 0: ばらばらに始まり終わる")
    shoot_density: FloatProperty(name="脇芽(本/m)", default=6.0, min=0.0, soft_max=60.0,
                                 description="つるから横に出る短い脇芽の数（1mあたり）")
    shoot_length: FloatProperty(name="脇芽の長さ", default=0.05, min=0.0, soft_max=0.5, unit="LENGTH")

    # --- density mode ----------------------------------------------------
    tangle_density: FloatProperty(name="密度(本/100cm²)", default=50.0, min=0.1, soft_max=400.0,
                                  description="起点の中心付近で、100cm²あたりに生えるつるの本数")
    concentration: FloatProperty(name="中心への集まり", default=0.35, min=0.0, max=1.0, subtype="FACTOR",
                                 description="0: 範囲内に均一 / 1: 起点の中心に強く集まる")
    tangle_length: FloatProperty(name="つるの長さ", default=0.1, min=0.005, soft_max=0.5, unit="LENGTH",
                                 description="1本のつるの平均の長さ")
    curl: FloatProperty(name="うねり", default=0.8, min=0.0, max=4.0,
                        description="つるが曲がりくねる強さ。大きいとループや渦を描いて絡まる")
    curl_length: FloatProperty(name="うねりの大きさ", default=0.025, min=0.001, soft_max=0.2, unit="LENGTH",
                               description="曲がりくねりの1つの弧の大きさ。小さいほど細かくうねる")
    containment: FloatProperty(name="範囲に留まる強さ", default=1.0, min=0.0, max=3.0,
                               description="範囲の外へ向かうつるを、密な方へ曲げて戻す強さ")
    fuse: FloatProperty(name="つながり", default=0.4, min=0.0, max=1.0, subtype="FACTOR",
                        description="つるが他のつるとつながる割合（出会ったとき・先端）。大きいほど先端の少ない網目（粘菌風）になる")
    fine_branch: FloatProperty(name="枝分かれ", default=0.5, min=0.0, max=5.0,
                               description="1本のつるが途中で枝分かれする回数の目安")
    fine_step: FloatProperty(name="細かさ（ステップ）", default=0.0025, min=0.0003, soft_max=0.02, unit="LENGTH",
                             description="つるの節の間隔。小さいほど細かく曲がる（重くなる）")
    fine_r_min: FloatProperty(name="最小の太さ", default=0.0004, min=0.00002, soft_max=0.005, unit="LENGTH")
    fine_r_max: FloatProperty(name="最大の太さ", default=0.002, min=0.0001, soft_max=0.01, unit="LENGTH")

    # --- where the vines go --------------------------------------------
    attractor_spacing: FloatProperty(
        name="密度（間隔）", default=0.02, min=0.003, soft_max=0.2, unit="LENGTH",
        description="つるが向かう目標点の間隔。小さいほど密に茂る（重くなる）")
    spread: FloatProperty(name="空間への広がり", default=0.02, min=0.0, soft_max=0.5, unit="LENGTH",
                          description="体の表面からどれだけ離れた空間までつるが広がるか")
    cling: FloatProperty(name="密着度", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                         description="0: 空間に均一に広がる / 1: ほとんど体に沿う")
    clearance: FloatProperty(name="体との最小距離", default=0.001, min=0.0, soft_max=0.05, unit="LENGTH",
                             description="つるの中心線が体に近づける最小の距離（貫通防止）")

    # --- how they grow --------------------------------------------------
    step: FloatProperty(name="成長ステップ", default=0.008, min=0.001, soft_max=0.05, unit="LENGTH")
    influence: FloatProperty(name="引き寄せ距離", default=0.1, min=0.005, soft_max=0.5, unit="LENGTH",
                             description="枝がどれだけ遠くの目標点に引き寄せられるか。大きいほど長く伸びて分岐が少ない")
    inertia: FloatProperty(name="直進性", default=0.6, min=0.0, max=0.95, subtype="FACTOR",
                           description="枝が前の向きを保つ度合い。大きいとなめらかな長い弧になる")
    wander: FloatProperty(name="揺らぎ", default=0.2, min=0.0, max=2.0,
                          description="成長方向のランダムな揺らぎ")
    meander: FloatProperty(name="くねり", default=0.35, min=0.0, max=1.5,
                           description="枝が左右にくねくねと曲がる強さ")
    meander_length: FloatProperty(name="くねりの周期", default=0.12, min=0.005, soft_max=0.5, unit="LENGTH",
                                  description="くねりの1往復の長さ")
    min_twig: FloatProperty(name="短い小枝を整理", default=0.035, min=0.0, soft_max=0.3, unit="LENGTH",
                            description="これより短い脇枝は取り除く（枝先は巻きひげになる）。0で整理しない")
    max_iterations: IntProperty(name="成長回数", default=400, min=10, max=5000)
    max_nodes: IntProperty(name="最大の節数", default=60000, min=100, max=500000)

    aerial_count: IntProperty(name="空中に伸びる枝", default=15, min=0, max=1000,
                              description="体から離れて空中に伸びる枝の本数")
    aerial_length: FloatProperty(name="空中の枝の長さ", default=0.15, min=0.0, soft_max=1.0, unit="LENGTH")
    aerial_lift: FloatProperty(name="上へ伸びる強さ", default=0.6, min=-2.0, max=3.0,
                               description="空中の枝が上（+Z）へ向かう強さ。負なら垂れ下がる")
    tendril_chance: FloatProperty(name="巻きひげの割合", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                                  description="枝先のうち、くるくる巻いた巻きひげで終わるものの割合")
    tendril_length: FloatProperty(name="巻きひげの長さ", default=0.04, min=0.0, soft_max=0.3, unit="LENGTH")

    # --- look ---------------------------------------------------------------
    r_min: FloatProperty(name="最小の太さ", default=0.0008, min=0.00005, soft_max=0.01, unit="LENGTH")
    r_max: FloatProperty(name="最大の太さ", default=0.0045, min=0.0002, soft_max=0.05, unit="LENGTH")
    pipe_exponent: FloatProperty(name="太さの変化", default=2.5, min=1.5, max=4.0,
                                 description="枝分かれでの太さの減り方（小さいほど根元が急に太くなる）")
    ring_res: IntProperty(name="断面分割数", default=6, min=3, max=16)
    color_thin: FloatVectorProperty(name="細い枝の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                    default=(0.45, 0.10, 0.10))
    color_thick: FloatVectorProperty(name="太い枝の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                     default=(0.22, 0.03, 0.04))
    roughness: FloatProperty(name="粗さ", default=0.45, min=0.0, max=1.0, subtype="FACTOR")
    subsurface: FloatProperty(name="透け感(SSS)", default=0.15, min=0.0, max=1.0, subtype="FACTOR")
    bump: FloatProperty(name="表面の凹凸", default=0.2, min=0.0, max=1.0, subtype="FACTOR")

    growth: FloatProperty(name="成長", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_growth_changed,
                          description="0: 起点だけ → 1: 全体。アニメーションには下の「成長（キーフレーム用）」を使う")
    last_message: bpy.props.StringProperty(default="")
    last_ok: BoolProperty(default=True)
    bind_mode: EnumProperty(
        name="追従方法",
        items=[("ARMATURE", "アーマチュア", "対象のボーンウェイトを転写してArmatureモディファイアで変形"),
               ("SURFACE", "サーフェス変形", "Surface Deformで対象の表面に貼り付ける"),
               ("NONE", "なし", "親子付けのみ")],
        default="ARMATURE")


classes = (VineGrowSettings,)

REACH = FloatProperty(name="範囲", default=0.0, min=0.0, soft_max=5.0, unit="LENGTH", update=_reach_changed,
                      description="起点から体に沿ってつるが広がる距離（ワールド単位）")
