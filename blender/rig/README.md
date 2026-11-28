# flower_v008 リグ

`flower_v008_rig.blend` は、`flower_v008.blend` に `rig_flower_fairy.py` を実行して作ったファイルです。

## 変更したもの（これ以外は元ファイルのまま）
- `fairy_geo`：ウェイトと Armature モディファイア（Subdivision の前）を追加
- `eye_L_geo` / `eye_R_geo`：`fairy_rig` の `head` にペアレント
- `fairy_grp` コレクションに `fairy_rig` と `fairy_tail_curve`（レンダーされない）を追加
- コントローラーの形状用メッシュを `fairy_rig_widgets` コレクションに追加（ビューレイヤーから除外済みなので、表示もレンダーもされない）
- `stomach_geo` と茎にシェイプキーを追加（値 0 なら元と同じ形）

花・茎・花びら・口・既存の `Armature`・カメラ・シーン設定には触っていません。

## 妖精 fairy_rig（ポーズモードで操作）
| 色 | コントローラー | 操作 |
|---|---|---|
| 黄 | `root`（口の底の穴の大きな輪） | 妖精全体を移動・回転 |
| 緑 | `tail_base`, `tail_ctrl.01`〜`06` | 蛇の部分。G で動かすと胴がなめらかにくねる。R で回すとカーブの向き、S でカーブの張りが変わる |
| 黄 | `torso`（腰の大きい輪） | 上半身ごと移動・回転（蛇部分の先端もついてくる） |
| 黄 | `hips` `spine` `spine.001` `chest` `neck` `head` | 体幹の FK（R で回す） |
| 青/赤 | `shoulder` `upper_arm` `forearm` `hand`（.L 青 / .R 赤） | 腕の FK |
| 橙 | `hand_ik.L/R`（手の箱）, `arm_pole.L/R`（肘の後ろの球） | 腕の IK。箱を動かすと手が付いてくる。球で肘の向きを決める |
| 青/赤 | `thumb` `index` `middle` `ring` `pinky` `.01〜.03` | 指の FK |

- **IK / FK の切り替え**：`hand_ik.L`（または `.R`）を選び、N パネル →「アイテム」→「プロパティ」の `IK_FK` を変えます。1 = IK（初期値）、0 = FK です。
- **蛇部分の仕組み**：`DEF-tail.001`〜`040`（40本、非表示）が、コントローラーで曲げたカーブに Spline IK で沿います。
- **骨コレクション**：Root / Tail / Torso / Body / Arms / Arm IK / Fingers / Deform（Deform は非表示）

## 胃袋（ボーンなし・シェイプキー）
`stomach_geo` を選び、プロパティ →「オブジェクトデータ」→「シェイプキー」で操作します。
- `膨張`：0〜2（1 で 15% 膨らむ）
- `収縮`：0〜2（1 で 15% 縮む）

値の横の ◇ を押すとキーが打てます。胃袋に絡む蔓の `胃袋_膨張` / `胃袋_収縮` は、このスライダーに自動で連動します。
