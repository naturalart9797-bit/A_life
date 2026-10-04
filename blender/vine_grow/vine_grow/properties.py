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


def _point_props():
    """Vine / leaf settings (kept as a group so they can be copied together)."""
    return {
        'tangle_length': FloatProperty(name="つるの長さ", default=0.3, min=0.005, soft_max=0.5, unit="LENGTH",
                                     description="1本のつるの平均の長さ"),
        'curl': FloatProperty(name="うねり", default=0.8, min=0.0, max=4.0,
                            description="つるが曲がりくねる強さ。大きいとループや渦を描いて絡まる"),
        'curl_length': FloatProperty(name="うねりの大きさ", default=0.025, min=0.001, soft_max=0.2, unit="LENGTH",
                                   description="曲がりくねりの1つの弧の大きさ。小さいほど細かくうねる"),
        'fine_branch': FloatProperty(name="枝分かれ", default=0.25, min=0.0, max=1.0, subtype="FACTOR",
                                   description="節ごとに脇枝が出る確率"),
        'internode': FloatProperty(name="節の間隔", default=0.025, min=0.003, soft_max=0.2, unit="LENGTH",
                                 description="茎の節（少しふくらむ所。脇枝・巻きひげが出る）の間隔"),
        'fine_aerial': FloatProperty(name="空中へ伸びる先端", default=0.2, min=0.0, max=1.0, subtype="FACTOR",
                                   description="つるの先端のうち、体を離れて空中へ伸びるものの割合"),
        'fine_step': FloatProperty(name="細かさ（ステップ）", default=0.0025, min=0.0003, soft_max=0.02, unit="LENGTH",
                                 description="つるの節の間隔。小さいほど細かく曲がる（重くなる）"),
        'fine_r_min': FloatProperty(name="最小の太さ", default=0.0004, min=0.00002, soft_max=0.005, unit="LENGTH"),
        'fine_r_max': FloatProperty(name="最大の太さ", default=0.002, min=0.0001, soft_max=0.01, unit="LENGTH"),
        'spread': FloatProperty(name="空間への広がり", default=0.035, min=0.0, soft_max=0.5, unit="LENGTH",
                              description="体の表面からどれだけ離れた空間までつるが広がるか"),
        'cling': FloatProperty(name="密着度", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                             description="0: 空間に均一に広がる / 1: ほとんど体に沿う"),
        'clearance': FloatProperty(name="体との最小距離", default=0.001, min=0.0, soft_max=0.05, unit="LENGTH",
                                 description="つるの中心線が体に近づける最小の距離（貫通防止）"),
        'aerial_lift': FloatProperty(name="上へ伸びる強さ", default=0.6, min=-2.0, max=3.0,
                                   description="空中の枝が上（+Z）へ向かう強さ。負なら垂れ下がる"),
        'tendril_chance': FloatProperty(name="巻きひげの割合", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                                      description="枝先のうち、くるくる巻いた巻きひげで終わるものの割合"),
        'color_thin': FloatVectorProperty(name="細い枝の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                        default=(0.45, 0.10, 0.10)),
        'color_thick': FloatVectorProperty(name="太い枝の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                         default=(0.22, 0.03, 0.04)),
        "leaf_chance": FloatProperty(name="葉の付き方", default=0.7, min=0.0, max=1.0, subtype="FACTOR",
                                     description="葉の位置ごとに、実際に葉が付く確率。0で葉なし"),
        "leaf_spacing": FloatProperty(name="葉の間隔", default=0.03, min=0.003, soft_max=0.3, unit="LENGTH",
                                      description="茎に沿った葉と葉の間隔"),
        "leaf_size": FloatProperty(name="葉の大きさ", default=0.022, min=0.001, soft_max=0.3, unit="LENGTH",
                                   description="葉身の長さ"),
        "leaf_size_var": FloatProperty(name="大きさのばらつき", default=0.4, min=0.0, max=1.0, subtype="FACTOR"),
        "leaf_tip_small": FloatProperty(name="先端ほど小さく", default=0.6, min=0.0, max=1.0, subtype="FACTOR",
                                        description="細い茎（先の方）の葉ほど小さくする"),
        "leaf_width": FloatProperty(name="葉の幅", default=0.85, min=0.2, max=1.6,
                                    description="長さに対する幅の比"),
        "leaf_lobe": FloatProperty(name="付け根の切れ込み", default=0.5, min=0.0, max=1.0, subtype="FACTOR",
                                   description="0: 卵形 / 1: 深いハート形"),
        "leaf_point": FloatProperty(name="先のとがり", default=0.5, min=0.0, max=1.0, subtype="FACTOR"),
        "leaf_petiole": FloatProperty(name="葉柄の長さ", default=0.45, min=0.0, max=2.0,
                                      description="葉の長さに対する葉柄（茎と葉をつなぐ柄）の長さ"),
        "leaf_cup": FloatProperty(name="丸まり", default=0.3, min=-1.0, max=1.0,
                                  description="葉の左右の反り（正: 内側に丸まる / 負: 外に反る）"),
        "leaf_fold": FloatProperty(name="中央の折れ", default=0.25, min=0.0, max=1.0, subtype="FACTOR",
                                   description="中央の葉脈で V 字に折れる強さ"),
        "leaf_droop": FloatProperty(name="垂れ", default=0.35, min=-1.0, max=1.5,
                                    description="葉先の垂れ下がり"),
        "leaf_face": FloatProperty(name="外を向く強さ", default=0.6, min=0.0, max=1.0, subtype="FACTOR",
                                   description="葉の表を体の外側（光の来る方）へ向ける強さ。0でばらばら"),
        "leaf_color": FloatVectorProperty(name="葉の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                          default=(0.05, 0.16, 0.03)),
        "leaf_color_young": FloatVectorProperty(name="若い葉の色", subtype="COLOR", size=3, min=0.0, max=1.0,
                                                default=(0.20, 0.32, 0.05),
                                                description="小さい葉（先の方）の色"),
    }


POINT_PROPS = tuple(_point_props().keys())


def _show_leaves_changed(self, context):
    from . import pipeline
    if self.target is not None:
        pipeline.sync_leaf_visibility(self.target, self.show_leaves)


def _show_vines_changed(self, context):
    from . import pipeline
    if self.target is not None:
        for o in pipeline.vine_objects(self.target):
            o.hide_viewport = not self.show_vines
            o.hide_render = not self.show_vines


def _redraw(self, context):
    for area in context.screen.areas if context.screen else ():
        if area.type == "VIEW_3D":
            area.tag_redraw()


class VineGrowSettings(bpy.types.PropertyGroup):
    target: PointerProperty(name="対象", type=bpy.types.Object, poll=_is_target,
                            description="つるを生やすメッシュオブジェクト")
    auto_scale: BoolProperty(name="サイズ自動スケール", default=True,
                             description="長さの値を「最大寸法1.7m」の物体を基準とし、対象の大きさに合わせて拡大縮小する")
    rest_pose: BoolProperty(name="レストポーズで処理", default=True)
    seed: IntProperty(name="シード", default=1, min=0)

    max_nodes: IntProperty(name="最大の節数", default=500000, min=100, max=20000000,
                           description="重くなりすぎないための上限。これに達するとそこで生成を止める")

    # --- look ---------------------------------------------------------------
    ring_res: IntProperty(name="断面分割数", default=6, min=3, max=16)
    roughness: FloatProperty(name="粗さ", default=0.45, min=0.0, max=1.0, subtype="FACTOR")
    subsurface: FloatProperty(name="透け感(SSS)", default=0.15, min=0.0, max=1.0, subtype="FACTOR")
    bump: FloatProperty(name="表面の凹凸", default=0.2, min=0.0, max=1.0, subtype="FACTOR")

    # --- density points (Yeti-style) --------------------------------------
    density: FloatProperty(name="全体の密度", default=50.0, min=0.0, soft_max=500.0,
                           description="値 100 で塗った所の密度（100cm² あたりのつるの本数）。塗った値はこれに対する割合")
    show_points: BoolProperty(name="点を表示", default=True, update=_redraw,
                              description="密度の点を、値に応じた色（紫=0 → 青 → 緑 → 黄 → オレンジ=100）で表示")
    show_vines: BoolProperty(name="つるを表示", default=True, update=_show_vines_changed,
                             description="つるの表示・非表示（点を塗るときは隠すと見やすい）")
    point_spacing: FloatProperty(name="点の間隔", default=0.015, min=0.002, soft_max=0.1, unit="LENGTH",
                                 description="散布・追加する点どうしの間隔")
    point_size: FloatProperty(name="点の表示サイズ", default=0.003, min=0.0002, soft_max=0.03, unit="LENGTH",
                              update=_redraw)
    point_default: FloatProperty(name="新しい点の値", default=0.0, min=0.0, max=100.0,
                                 description="散布・追加した点の最初の値（0〜100）")
    brush_tool: EnumProperty(
        name="ブラシ", default="PAINT",
        items=[("PAINT", "塗る", "点の密度を塗る（Ctrl: 減らす / Shift: ぼかす）"),
               ("ADD", "追加", "点を追加（Ctrl: 削除）"),
               ("REMOVE", "削除", "点を削除")])
    brush_value: FloatProperty(name="値", default=100.0, min=0.0, max=100.0,
                               description="塗る値（0〜100）。100 で「全体の密度」になる")
    brush_radius: IntProperty(name="半径(px)", default=60, min=2, max=1000)
    brush_strength: FloatProperty(name="強さ", default=0.5, min=0.0, max=1.0, subtype="FACTOR")
    add_with_value: BoolProperty(name="追加した点をブラシの値で塗る", default=False)
    point_blend: FloatProperty(name="なじませる距離", default=0.02, min=0.0, soft_max=0.2, unit="LENGTH",
                               description="隣り合う点の密度をなめらかにつなぐ距離")

    show_leaves: BoolProperty(name="葉を表示", default=True, update=_show_leaves_changed,
                              description="葉の表示・非表示（ビューポートとレンダー）")
    leaf_translucency: FloatProperty(name="葉の透け感", default=0.3, min=0.0, max=1.0, subtype="FACTOR")
    leaf_roughness: FloatProperty(name="葉の粗さ", default=0.4, min=0.0, max=1.0, subtype="FACTOR")

    growth: FloatProperty(name="成長", default=1.0, min=0.0, max=1.0, subtype="FACTOR", update=_growth_changed,
                          description="0: なし → 1: 全体（つるは根元から伸びる）。アニメーションには下の「成長（キーフレーム用）」を使う")
    last_message: bpy.props.StringProperty(default="")
    last_ok: BoolProperty(default=True)
    bind_mode: EnumProperty(
        name="追従方法",
        items=[("ARMATURE", "アーマチュア", "対象のボーンウェイトを転写してArmatureモディファイアで変形"),
               ("SURFACE", "サーフェス変形", "Surface Deformで対象の表面に貼り付ける"),
               ("NONE", "なし", "親子付けのみ")],
        default="ARMATURE")


VineGrowSettings.__annotations__.update(_point_props())


classes = (VineGrowSettings,)
