import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)


def _is_mesh(_self, obj):
    return obj.type == "MESH" and not obj.get("vine_dress_source")


class VineDressSettings(bpy.types.PropertyGroup):
    # --- target -------------------------------------------------------
    body: PointerProperty(name="人物メッシュ", type=bpy.types.Object, poll=_is_mesh,
                          description="つるを纏わせる人物のメッシュ")
    seed: IntProperty(name="シード", default=1, min=0)
    auto_scale: BoolProperty(
        name="身長で自動スケール", default=True,
        description="長さの値を身長1.7m基準として、人物の実際の高さに合わせて拡大縮小する")
    rest_pose: BoolProperty(
        name="レストポーズで生成", default=True,
        description="アーマチュアをレストポーズにしてから生成・バインドする（推奨）")
    mask_group: StringProperty(
        name="生成範囲グループ",
        description="このウェイトが0.5以上の領域だけにつるを生やす（空なら全身）。顔や手を除外するのに使う")

    # --- heights (fraction of body height: 0 = feet, 1 = head top) -----
    cover_top: FloatProperty(name="上端の高さ", default=0.82, min=0.0, max=1.0, subtype="FACTOR",
                             description="つるが覆う上限（身長に対する割合）。0.82≒肩/首")
    water_object: PointerProperty(name="水面オブジェクト", type=bpy.types.Object,
                                  description="指定するとこのオブジェクトのZ位置を水面とする")
    water_level: FloatProperty(name="水面の高さ", default=0.55, min=-0.5, max=1.5, subtype="FACTOR",
                               description="水面の高さ（身長に対する割合）。水面オブジェクトが無い場合に使用")
    skirt_at_water: BoolProperty(name="スカート開始＝水面", default=True,
                                 description="スカートの広がり開始位置を水面の高さに合わせる")
    skirt_top: FloatProperty(name="スカート開始", default=0.55, min=0.0, max=1.0, subtype="FACTOR",
                             description="スカートが始まる高さ（身長に対する割合）。腰≒0.5〜0.58")
    skirt_bottom: FloatProperty(name="スカート裾", default=-0.02, min=-1.0, max=1.0, subtype="FACTOR",
                                description="スカートの裾の高さ（身長に対する割合）。0=足元")

    # --- body vines ----------------------------------------------------
    vine_count: IntProperty(name="つるの本数/パス", default=24, min=1, max=500)
    coverage_passes: IntProperty(name="被覆パス数", default=6, min=1, max=20,
                                 description="隙間を探して新しいつるを追加する回数。増やすと密に覆う")
    spacing: FloatProperty(name="つる間隔", default=0.022, min=0.002, soft_max=0.2, unit="LENGTH",
                           description="つる同士の最小距離。小さいほど密に覆う")
    step_length: FloatProperty(name="ステップ長", default=0.01, min=0.001, soft_max=0.05, unit="LENGTH")
    max_steps: IntProperty(name="最大長(ステップ)", default=180, min=4, max=5000)
    wrap: FloatProperty(name="巻き付き", default=0.7, min=0.0, max=3.0,
                        description="体の周りを水平に巻き付く傾向")
    spiral_bias: FloatProperty(name="巻き方向の偏り", default=0.0, min=-1.0, max=1.0,
                               description="-1=全て逆巻き, 0=ランダム, 1=全て同方向")
    vertical_bias: FloatProperty(name="上下傾向", default=0.25, min=-2.0, max=2.0,
                                 description="正=上へ伸びる, 負=下へ垂れる")
    persistence: FloatProperty(name="直進性", default=1.0, min=0.0, max=5.0)
    noise: FloatProperty(name="うねり", default=0.45, min=0.0, max=3.0)
    branch_chance: FloatProperty(name="分岐確率", default=0.03, min=0.0, max=0.5)
    max_depth: IntProperty(name="分岐の深さ", default=2, min=0, max=5)
    branch_radius: FloatProperty(name="枝の太さ比", default=0.7, min=0.1, max=1.0)
    radius: FloatProperty(name="つるの太さ", default=0.005, min=0.0002, soft_max=0.05, unit="LENGTH")
    taper: FloatProperty(name="先細り", default=0.6, min=0.0, max=1.0, subtype="FACTOR")
    surface_offset: FloatProperty(name="肌からの距離", default=0.002, min=0.0, soft_max=0.05,
                                  unit="LENGTH")

    # --- skirt ---------------------------------------------------------
    use_skirt: BoolProperty(name="スカートを生成", default=True)
    skirt_vines: IntProperty(name="縦つるの本数", default=36, min=1, max=1000)
    skirt_weave: BoolProperty(name="編み込み(交差)", default=True,
                              description="右巻きと左巻きのつるを交互に配置して網目にする")
    skirt_rings: IntProperty(name="横つるの本数", default=3, min=0, max=100)
    flare: FloatProperty(name="広がり", default=0.35, min=0.0, soft_max=2.0,
                         description="裾での広がり量（身長に対する割合）")
    flare_power: FloatProperty(name="広がりカーブ", default=1.6, min=0.3, max=5.0,
                               description="1=円錐, 大きいほど裾で急に広がるベル形")
    twist: FloatProperty(name="ねじれ(回転数)", default=0.35, min=-3.0, max=3.0)
    ruffle: FloatProperty(name="フリル", default=0.08, min=0.0, max=1.0)
    ruffle_freq: IntProperty(name="フリル数", default=7, min=1, max=64)
    skirt_wave: FloatProperty(name="ゆらぎ", default=0.3, min=0.0, max=3.0)
    skirt_ragged: BoolProperty(name="裾を不揃いに", default=True)
    skirt_follow_body: BoolProperty(name="脚を貫通しない", default=True,
                                    description="スカートの半径を体の外形より内側にしない")
    skirt_body_group: StringProperty(
        name="スカート追従グループ",
        description="スカートの外形判定・ウェイト取得に使う頂点グループ（例: 腰と脚）。空なら全身。"
                    "腕や手がスカートに影響する場合に指定")



    # --- guides --------------------------------------------------------
    gen_body_guides: BoolProperty(name="上半身ガイド", default=True,
                                  description="体表面を這うガイドカーブを自動生成する")
    gen_skirt_guides: BoolProperty(name="スカートガイド", default=True,
                                   description="水中スカートのガイドカーブを自動生成する")
    guide_point_spacing: FloatProperty(
        name="制御点の間隔", default=0.03, min=0.005, soft_max=0.5, unit="LENGTH",
        description="自動生成ガイドの制御点の間隔。大きいほど編集しやすい（点が少ない）")

    # --- groom ---------------------------------------------------------
    groom_tree: PointerProperty(name="グルームツリー", type=bpy.types.NodeTree,
                                poll=lambda self, t: t.bl_idname == "VineGroomTreeType")
    active_group_index: IntProperty(name="アクティブグループ", default=-1)

    brush_radius: IntProperty(name="半径(px)", default=50, min=5, max=500, subtype="PIXEL",
                              description="ブラシの半径。[ ] キーで変更")
    brush_strength: FloatProperty(name="強さ", default=0.5, min=0.0, max=1.0, subtype="FACTOR")
    brush_selected_only: BoolProperty(name="選択のみ", default=False,
                                      description="選択中のガイドがあれば、それだけに作用する")
    brush_xray: BoolProperty(name="裏側にも作用", default=False,
                             description="体の陰に隠れたガイドにも作用する")
    comb_keep_length: BoolProperty(name="長さを維持", default=True,
                                   description="コームでガイドの長さを保つ")

    show_overlay: BoolProperty(name="ガイドを表示", default=True)
    overlay_xray: BoolProperty(name="透過表示", default=False, description="体に隠れたガイドも表示する")
    overlay_in_pose: BoolProperty(name="ポーズ中も表示", default=False,
                                  description="ガイドはレストポーズ基準なので、通常はポーズ中は非表示にする")
    overlay_line_width: FloatProperty(name="線の太さ", default=2.0, min=1.0, max=8.0)
