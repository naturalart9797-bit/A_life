# Maya Style Tools (Blender アドオン)

Blender の編集モードでの選択操作と UV 展開を Maya に近づけるアドオンです。

## 機能

| 機能 | 操作 | 内容 |
|---|---|---|
| Symmetry | サイドバー(N) > Maya > `Symmetry` | 片側を選択すると反対側も自動で選択/解除される。クリック・ボックス・サークル・投げ縄・ループ・パス選択など、どの選択方法でも有効。頂点・辺・面すべてに対応 |
| Mirror Transform | Symmetry の下のチェック | Blender のメッシュミラー（X/Y/Z）も同時にオンにして、移動・回転・拡大も左右対称にする |
| Double-Click Loop | 既定でオン | 辺をダブルクリックでエッジループ選択 |
| Shift + Double-Click Path | 既定でオン | 辺（頂点・面も可）を選んだ後、別の辺を Shift + ダブルクリックすると、その間の最短経路を選択（Maya と同じ操作）。続けて Shift + ダブルクリックすると経路を延ばせる |
| Mirror Current Selection | ボタン | 今の選択に反対側をまとめて追加 |

対称軸（X/Y/Z, オブジェクトのローカル座標）と、反対側の頂点と見なす距離の許容値（Tolerance）を設定できます。

## UV Toolkit（Maya の UV Toolkit 相当）

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

* Blender 4.2 以降: `blender_maya_select.zip` を `編集 > プリファレンス > アドオン > ▼ > ディスクからインストール` で選択
* Blender 3.x〜4.1: 同じ zip を `プリファレンス > アドオン > インストール` で選択して有効化

## 注意

* Unfold は、Maya と同じくシェル単位で展開するため、実行前にシェルの境界をシームとして付けます。
* Checker はビューレイヤーのマテリアル上書きを使います。表示中はすべてのオブジェクトがチェッカーになります（もう一度押すと元に戻ります）。

* 反対側の対応は頂点位置で判定します。形が左右対称でない部分（対応する頂点がない部分）はミラーされません。
* 対称化はトポロジー（頂点数など）が変わったときに自動で作り直します。
