# Maya Style UV Toolkit (Blender アドオン)

Blender の UV 展開を Maya の UV Toolkit に近づけるアドオンです。
選択まわり（Symmetry やループ / 経路選択）は別アドオン [Maya Style Select](../blender_maya_select/README.md) です。

UV エディターのサイドバー（N）> **UV Toolkit** タブに、Maya の UV Toolkit と同じ並びでまとめています。3D ビューのサイドバー > Maya > **UV** にも、UV エディターを開くボタン・チェッカー・カット/縫合・投影があります。

| セクション | 中身 |
|---|---|
| (上部) | **Checker**（全オブジェクトにチェッカー表示。元のマテリアルは書き換えない）/ **Distortion**（UV の歪みを色表示）。UV エディターのヘッダーにも同じボタンあり |
| Pin | Pin / Unpin / Invert / Select Pinned |
| Select | Shell / Overlapping / Grow / Shrink。**ダブルクリックでシェル選択、Shift + ダブルクリックで最短経路選択** |
| Transform | 矢印ボタンで移動（ステップ指定）、指定角度で回転（既定 90°）、Flip U/V、Per Shell（シェルごとの中心で回転・反転）、Align Min/Avg/Max U/V、Straighten UV/U/V、Distribute U/V、Orient Shells / Orient to Edge、Stack / Unstack |
| Create | Planar X/Y/Z/Best（平均法線）、Camera-based、Cylindrical、Spherical、Cube、Automatic |
| Cut and Sew | Cut（シームを付ける）、Sew（中間で縫合）、Move and Sew（シェルを動かして縫合）、Split、Create Shell、Seams from Shells |
| Unfold | **Unfold**（Maya と同じく、シェルの位置と大きさを保ったまま展開し直す）/ Optimize（歪みを緩和、反復回数指定） |
| Arrange and Layout | Layout（0-1 に詰める、余白・回転指定）/ Normalize Scale |
| Texel Density | マップサイズを指定して Get / Set（px/m） |
| Display | チェッカーの種類と解像度、歪み表示の種類、面の塗り表示 |

## インストール

* Blender 4.2 以降: `blender_maya_uv.zip` を `編集 > プリファレンス > アドオン > ▼ > ディスクからインストール` で選択
* Blender 3.x〜4.1: 同じ zip を `プリファレンス > アドオン > インストール` で選択して有効化

## 注意

* Unfold は、Maya と同じくシェル単位で展開するため、実行前にシェルの境界をシームとして付けます。
* Checker はビューレイヤーのマテリアル上書きを使います。表示中はすべてのオブジェクトがチェッカーになります（もう一度押すと元に戻ります）。
* Orient Shells / Orient to Edge は Blender に該当機能がある版（4.2 以降）でだけ表示されます。
