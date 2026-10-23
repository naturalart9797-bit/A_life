# Arthur Rackham 風 Ink & Wash シェーダー (Blender)

`rackham_shader.py` は、アーサー・ラッカムの挿絵みたいな
「少し褪せた水彩ウォッシュ + 細く滑らかなペンの輪郭線 + ムラのある紙」
のルックを Blender で作るスクリプトです。

対応: Blender 4.2 以降 / 5.x（EEVEE）。`Shader to RGB` を使うので **Cycles では動きません**。

![preview](rackham_preview.png)

（デモシーンを EEVEE で 1280×720 レンダリングした例）

## 使い方

### GUI
1. Scripting タブで `rackham_shader.py` を開いて **Run Script**
2. 動作
   - メッシュを選択している → そのマテリアルの Base Color を引き継ぎ、Rackham マテリアルに置き換える
   - 何も選択していない → デモシーン（ねじれた古木・ゴブリン・岩）を作成
3. レンダー設定（EEVEE、ビュー変換 Standard、ワールドに紙、Freestyle）は自動で設定されます

### コマンドライン
```
blender -b -P rackham_shader.py -- --demo --render //rackham.png
blender -b myscene.blend -P rackham_shader.py -- --render //out.png   # シーン内の全メッシュに適用
```

## しくみ

| 要素 | 実装 |
|---|---|
| 水彩トーン | Diffuse → Shader to RGB で光量を取り、柔らかい smoothstep で「紙 / 中間ウォッシュ / 薄い影」に分ける（影は黒くせず中間調のアンバー） |
| 褪せた顔料 | Base Color の彩度を少し落とし、紙色に乗算（セピアには寄せすぎない） |
| ウォッシュのムラ | オブジェクト座標のノイズで光量を揺らし、帯の境界をにじませる |
| 顔料の粒状感・縁の溜まり | Layer Weight(Facing) で縁に影色を重ねる。粒状感は `Granulation` で（初期値オフ） |
| ハッチング | 初期値はオフ（`Hatch Strength` = 0）。上げると暗部に細い斜線が入る |
| 紙・背景 | スクリーン座標のノイズでグレイン。背景は雲状にムラのある灰ベージュのウォッシュ＋周辺減光 |
| 輪郭線 | Freestyle。ベジェで均した細く滑らかなほぼ黒のペン線（入り抜き付き）。Suggestive Contours で襞や窪みなど内側の線も描く |

## 主なパラメータ（グループノード `Rackham Ink & Wash`）

- **Base Color** – 固有色。彩度高めで入れても自動で褪色します
- **Paper Color / Ink Color / Shadow Tint** – 紙・インク・影の色
- **Desaturate** – 褪色の強さ（初期値 0.2。上げるほど古びた印象に）
- **Light / Shadow Threshold** – 紙が見える明部 / 影の境界
- **Highlight Wash** – 明部にどれだけ薄く色を残すか
- **Hatch Threshold / Cross Hatch Threshold** – ハッチが入り始める暗さ
- **Hatch Density / Width / Wobble / Strength / Breakup** – ハッチの密度・太さ・揺れ・濃さ・途切れ具合
- **Wash Variation / Wash Scale** – 水彩ムラの強さ・大きさ
- **Paper Grain** – 紙のザラつき
- **Aspect** – レンダー解像度の縦横比（スクリプト実行時に自動設定。解像度を変えたら再実行かここを修正）

輪郭線の太さや揺れは Freestyle の Line Style `Rackham Pen` で調整します
（View Layer プロパティ → Freestyle Line Style）。
線をもっと細くするには `Thickness`（初期値 1.1）を下げる。内側の線が多すぎる場合は
View Layer → Freestyle の Suggestive Contours をオフに。

## コツ

- ライトは Sun 1灯が基本
- 木の幹・根・岩など凹凸の多いモデルほど Freestyle の線が効いてラッカムっぽくなります
- 固有色は鮮やかすぎない中間色（オリーブ・くすんだ青灰・黄土など）を選ぶと雰囲気が出ます
