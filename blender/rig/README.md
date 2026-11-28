# flower_v008 リグ

`flower_v008_rig.blend` は `flower_v008.blend` に `rig_flower_fairy.py` を実行して作ったファイルです（元ファイルはそのまま残してあります）。
モデルを修正したら、元ファイルを開いて Scripting タブでこのスクリプトを実行すれば、リグを作り直せます。

## flower_rig（花）
| ボーン | 役割 |
|---|---|
| `flower_root` | 全体の移動。地面の根はこれに固定 |
| `stalk_01`〜`05` | 胃袋より上の茎（FK）。回転させるとしなる |
| `flower_head` | 花の頭（花びら・口）。妖精もここにぶら下がる |
| `stomach_ctrl` | 胃袋の鼓動のプロパティ（N パネル → アイテム → プロパティ） |

`stomach_ctrl` のプロパティ:
- `auto_beat`: 1 = 再生するだけで自動で鼓動、0 = 止める
- `bpm`: 鼓動の速さ
- `amount`: 膨らみの強さ
- `wobble`: 6 方向のローブがずれて動く量（バクバク感）
- `beat`: 手動の膨らみ。キーを打って好きなタイミングで動かせる

胃袋に絡んでいる蔓は、胃袋の表面と一緒に動きます。

## fairy_rig（妖精）
| ボーン | 役割 |
|---|---|
| `fairy_root` | 花の穴の根元。全体の基点 |
| `tail_ctrl_base`, `tail_ctrl_01`〜`03`, `body_ctrl` | 柄（蛇の部分）のカーブを動かすコントローラー。`body_ctrl` は上半身ごと動く |
| `hips` `spine` `chest` `neck` `head` | 体の FK |
| `clavicle` `upper_arm` `forearm` `hand` (.L/.R) | 腕の FK |
| `hand_ik.L/R`, `arm_pole.L/R` | 腕の IK。`hand_ik` のプロパティ `ik_fk` を 1 にすると IK になる |

柄は `tail_01`〜`16` に Spline IK がかかっていて、`fairy_tail_curve` に沿って動きます（非表示コレクション `Tail Deform`）。
目は `head` ボーンにペアレントされています。
