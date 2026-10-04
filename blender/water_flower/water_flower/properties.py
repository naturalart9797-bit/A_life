# SPDX-License-Identifier: GPL-3.0-or-later
"""花の設定 (オブジェクトごと)。値を変えるとその場で形を作り直す。"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
    PointerProperty,
)


def _regen(self, context):
    if self.lock_update or not self.is_flower:
        return
    from . import build
    build.regenerate(self.id_data)


def _apply_preset(self, context):
    if self.preset == 'CUSTOM':
        return
    values = PRESETS.get(self.preset, {})
    self.lock_update = True
    try:
        for k in DEFAULTS:
            setattr(self, k, DEFAULTS[k])
        for k, v in values.items():
            setattr(self, k, v)
    finally:
        self.lock_update = False
    _regen(self, context)


def F(name, default, lo, hi, desc="", unit='NONE', subtype='NONE', step=None):
    kw = dict(name=name, default=default, min=lo, max=hi, description=desc,
              update=_regen, subtype=subtype, unit=unit)
    if step is not None:
        kw["step"] = step
    return FloatProperty(**kw)


def I(name, default, lo, hi, desc=""):
    return IntProperty(name=name, default=default, min=lo, max=hi, description=desc, update=_regen)


def B(name, default, desc=""):
    return BoolProperty(name=name, default=default, description=desc, update=_regen)


def C(name, default):
    return FloatVectorProperty(name=name, default=default, subtype='COLOR', size=3,
                               min=0.0, max=1.0, update=_regen)


# (属性名, プロパティ) — UI の並びもこの順
SPEC = {
    # 全体
    "seed": I("シード", 3, 0, 100000, "ばらつきの乱数"),
    "bloom": F("開き具合", 1.0, 0.0, 1.0, "1=満開, 0=つぼみ", subtype='FACTOR'),

    # 器 (水をためる中央の花びら)
    "corona_radius": F("器の半径", 0.15, 0.01, 10.0, "器の胴の半径", unit='LENGTH'),
    "corona_height": F("器の高さ", 0.5, 0.01, 10.0, "底から縁までの高さ", unit='LENGTH'),
    "corona_bottom": F("底の丸み", 0.35, 0.05, 0.9, "大きいほど丸い底 (小さいと平らな底)"),
    "corona_bulge": F("胴のふくらみ", 0.05, -0.5, 1.0, "風呂のような丸い胴"),
    "corona_flare": F("縁の広がり", 0.8, -0.5, 3.0, "縁がラッパのように開く量"),
    "corona_lobes": I("縁の切れ込み数", 6, 0, 24, "器を作る花びらの枚数 (縁の山の数)"),
    "corona_lobe_depth": F("切れ込みの深さ", 0.04, 0.0, 0.3, "縁の山と谷の高さの差 (器の高さ比)"),
    "corona_ribs": I("筋の数", 22, 0, 120, "器の縦の筋"),
    "corona_rib_depth": F("筋の深さ", 0.02, 0.0, 0.2),
    "rim_frill": F("縁のフリル", 0.1, 0.0, 0.3, "縁の波打ち"),
    "rim_frill_freq": I("フリルの数", 16, 0, 80),
    "corona_res_t": I("器の解像度 (縦)", 40, 6, 200),
    "corona_res_phi": I("器の解像度 (周)", 112, 12, 512),

    # 外側の花びら
    "whorls": I("層の数", 2, 1, 4, "外側の花びらの重なりの層"),
    "petals_per_whorl": I("1 層の枚数", 3, 1, 16),
    "petal_rotation": F("回転", 0.0, -6.2832, 6.2832, subtype='ANGLE'),
    "petal_length": F("長さ", 1.0, 0.01, 20.0, unit='LENGTH'),
    "petal_width": F("幅", 0.26, 0.02, 1.5, "長さに対する半幅"),
    "petal_widest": F("最大幅の位置", 0.4, 0.05, 0.95),
    "petal_pointiness": F("先の尖り", 1.2, 0.1, 3.0, "小さいほど丸い先"),
    "petal_claw": F("付け根の細さ", 0.5, 0.0, 1.0),
    "petal_tilt": F("開いた角度", 15.0, -60.0, 89.0, "水平からの角度 (度)"),
    "petal_curl": F("反り", -0.4, -3.0, 3.0, "− で外へ反る / + で内へ巻く"),
    "petal_cup": F("くぼみ", 0.25, -1.5, 2.0, "幅方向の丸まり"),
    "petal_twist": F("ねじれ", 1.3, -6.0, 6.0, "先へ行くほどねじれる (ラジアン)"),
    "twist_alternate": B("ねじれを交互に", True),
    "petal_wave": F("うねり", 0.07, 0.0, 0.5, "長さ方向の波"),
    "petal_wave_freq": F("うねりの回数", 1.2, 0.0, 6.0),
    "petal_ruffle": F("縁の波", 0.06, 0.0, 1.0),
    "petal_ruffle_freq": F("縁の波の数", 3.0, 0.0, 20.0),
    "petal_midrib": F("中央の筋", 0.02, 0.0, 0.3),
    "petal_wrap_open": F("開いた時の巻き付き", 0.12, 0.0, 1.0, "器のまわりに沿う量"),
    "petal_attach": F("付く高さ", 0.12, 0.0, 0.9, "器の高さに対する付け根の位置"),
    "petal_jitter": F("ばらつき", 0.3, 0.0, 1.0),
    "whorl_tilt_step": F("外の層ほど倒す", -6.0, -45.0, 45.0, "層ごとの角度差 (度)"),
    "whorl_length_step": F("外の層ほど長く", 0.05, -0.5, 1.0),
    "whorl_width_step": F("外の層ほど幅広く", 0.0, -0.5, 1.0),
    "petal_res_u": I("花びらの解像度 (長さ)", 40, 4, 200),
    "petal_res_v": I("花びらの解像度 (幅)", 14, 2, 100),

    # つぼみ
    "bud_tilt": F("つぼみの角度", 86.0, 30.0, 100.0, "閉じた時の立ち上がり (度)"),
    "bud_close": F("つぼみの閉じ具合", 0.85, 0.0, 1.0, "閉じた時に花びらの先がどこまで軸に寄るか", subtype='FACTOR'),
    "bud_spiral": F("つぼみのらせん", 0.5, -3.0, 3.0, "閉じた時に先へ行くほど軸のまわりに回る (ラジアン)"),
    "bud_scale": F("つぼみの大きさ", 0.8, 0.2, 1.0, "閉じた時の花びらの長さの比"),
    "bud_pinch": F("器のすぼまり", 0.3, 0.0, 0.9, "閉じた時に器の口がすぼまる量"),
    "bud_flare": F("つぼみの縁の広がり", 0.0, 0.0, 1.0, "閉じた時に残る縁の広がり (1=そのまま)"),

    # しべ
    "stamen_count": I("しべの数", 6, 0, 40),
    "stamen_height": F("しべの高さ", 1.0, 0.1, 3.0, "器の高さ比"),
    "stamen_spread": F("しべの広がり", 0.3, 0.0, 2.0),
    "stamen_curve": F("しべの曲がり", 0.3, -3.0, 3.0),
    "stamen_radius": F("しべの太さ", 0.005, 0.0005, 0.1, unit='LENGTH'),
    "stamen_anther": F("葯の太さ", 0.012, 0.001, 0.2, unit='LENGTH'),

    # 茎
    "stem": B("茎", True),
    "stem_length": F("茎の長さ", 1.2, 0.0, 50.0, unit='LENGTH'),
    "stem_radius": F("茎の太さ", 0.16, 0.01, 2.0, "器の半径比"),
    "stem_bend": F("茎の曲がり", 0.1, -1.0, 1.0),
    "ovary_radius": F("子房のふくらみ", 0.32, 0.01, 2.0, "器の下のふくらみ (器の半径比)"),
    "ovary_length": F("子房の長さ", 0.6, 0.0, 5.0, "器の半径比"),

    # 仕上げ
    "thickness": F("厚み", 0.006, 0.0, 0.2, "Solidify の厚み", unit='LENGTH'),
    "clearance": F("すき間", 0.012, 0.0005, 0.5, "部品どうしの最小のすき間 (厚みより大きく)", unit='LENGTH'),
    "whorl_gap": F("層のすき間", 0.012, 0.0, 0.5, "層ごとに外側へずらす量", unit='LENGTH'),
    "overlap_gap": F("重なりのすき間", 0.016, 0.0, 0.5, "隣の花びらと重なる所のずれ", unit='LENGTH'),
    "smooth": B("スムーズシェード", True),

    # 水
    "show_water": B("水を表示", True),
    "water_fill": F("満水率", 0.92, 0.0, 1.0, "縁のいちばん低い所に対する水位", subtype='FACTOR'),
    "water_margin": F("縁との余裕", 0.004, 0.0, 1.0, unit='LENGTH'),
    "water_gap": F("壁とのすき間", 0.0005, 0.0, 0.1, unit='LENGTH'),

    # 色
    "color_petal_base": C("花びら (付け根)", (1.0, 0.62, 0.05)),
    "color_petal_tip": C("花びら (先)", (1.0, 0.82, 0.25)),
    "color_corona_base": C("器 (底)", (1.0, 0.3, 0.0)),
    "color_corona_rim": C("器 (縁)", (1.0, 0.5, 0.05)),
    "color_stamen": C("しべ", (0.95, 0.75, 0.1)),
    "color_stem": C("茎", (0.25, 0.45, 0.15)),

    # アニメーション
    "anim_stagger": F("層の時間差", 0.3, 0.0, 0.9, "外側の層ほど先に閉じる"),
}

DEFAULTS = {}
for _k, _p in SPEC.items():
    DEFAULTS[_k] = _p.keywords["default"]

PRESETS = {
    "DAFFODIL": {},  # 既定値 (スイセン風)
    "BATH": dict(
        corona_radius=0.28, corona_height=0.26, corona_bottom=0.6, corona_bulge=0.3,
        corona_flare=0.25, corona_lobes=8, corona_lobe_depth=0.03, corona_ribs=0,
        rim_frill=0.03, rim_frill_freq=8, petals_per_whorl=5, petal_length=0.7,
        petal_width=0.28, petal_twist=0.5, petal_tilt=8.0, petal_wave=0.03,
        stamen_count=3, stamen_height=0.8,
        color_petal_base=(0.95, 0.5, 0.65), color_petal_tip=(1.0, 0.85, 0.9),
        color_corona_base=(0.75, 0.2, 0.4), color_corona_rim=(1.0, 0.6, 0.7),
        color_stamen=(1.0, 0.95, 0.7)),
    "CHALICE": dict(
        corona_radius=0.14, corona_height=0.55, corona_bottom=0.25, corona_bulge=0.05,
        corona_flare=0.7, corona_lobes=5, corona_lobe_depth=0.05, corona_ribs=24,
        rim_frill=0.08, rim_frill_freq=20, petals_per_whorl=4, petal_length=0.9,
        petal_width=0.22, petal_tilt=25.0, petal_curl=-0.6, petal_twist=1.4,
        stamen_height=0.9,
        color_petal_base=(0.95, 0.95, 0.9), color_petal_tip=(1.0, 1.0, 1.0),
        color_corona_base=(0.9, 0.35, 0.05), color_corona_rim=(1.0, 0.5, 0.2)),
    "PLAY": dict(
        whorls=3, petals_per_whorl=4, petal_length=0.95, petal_width=0.26,
        petal_twist=2.2, petal_wave=0.12, petal_wave_freq=2.0, petal_ruffle=0.15,
        petal_ruffle_freq=5.0, petal_curl=-0.8, petal_tilt=20.0, petal_jitter=0.7,
        whorl_tilt_step=-12.0, whorl_length_step=0.15, rim_frill=0.09,
        rim_frill_freq=22, corona_flare=0.6,
        color_petal_base=(0.55, 0.35, 0.95), color_petal_tip=(0.75, 0.85, 1.0),
        color_corona_base=(0.2, 0.3, 0.9), color_corona_rim=(0.4, 0.8, 1.0)),
}

ann = dict(SPEC)
ann.update(
    is_flower=BoolProperty(default=False, options={'HIDDEN'}),
    lock_update=BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'}),
    water_object=PointerProperty(type=bpy.types.Object, name="水"),
    water_volume=FloatProperty(name="容量", default=0.0),
    water_level=FloatProperty(name="水位", default=0.0),
    anim_baked=BoolProperty(default=False),
    anim_stale=BoolProperty(default=False),
    preset=EnumProperty(
        name="プリセット",
        items=[
            ('CUSTOM', "カスタム", "今の値のまま"),
            ('DAFFODIL', "スイセン", "ラッパ形の器とねじれた花びら"),
            ('BATH', "風呂", "浅く丸い器とたくさんの花びら"),
            ('CHALICE', "聖杯", "細く深い器"),
            ('PLAY', "あそび", "大きくねじれて波打つ 3 層の花びら"),
        ],
        default='CUSTOM', update=_apply_preset,
    ),
    anim_mode=EnumProperty(
        name="動き",
        items=[
            ('CLOSE', "閉じる", "満開 → つぼみ"),
            ('OPEN', "開く", "つぼみ → 満開"),
            ('CLOSE_OPEN', "閉じて開く", "満開 → つぼみ → 満開"),
            ('OPEN_CLOSE', "開いて閉じる", "つぼみ → 満開 → つぼみ"),
        ],
        default='CLOSE_OPEN',
    ),
    anim_start=IntProperty(name="開始フレーム", default=1),
    anim_duration=IntProperty(name="動く長さ", default=60, min=2, description="開く / 閉じるのにかかるフレーム数"),
    anim_hold=IntProperty(name="止まる長さ", default=24, min=0, description="閉じて開くときの途中で止まるフレーム数"),
    anim_samples=IntProperty(name="段階数", default=11, min=3, max=60,
                             description="途中の形を何段階で記録するか (多いほどなめらか)"),
)

WaterFlowerSettings = type("WaterFlowerSettings", (bpy.types.PropertyGroup,), {"__annotations__": ann})

classes = (WaterFlowerSettings,)
