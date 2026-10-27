# Maya Style UV Toolkit (Blender アドオン)

Blender の UV 展開を Maya の UV Toolkit とマーキングメニューに近づけるアドオンです。
選択まわり（Symmetry やループ / 経路選択）は別アドオン [Maya Style Select](../blender_maya_select/README.md) です。

## 場所

* **UV エディター > サイドバー（N）> UV Toolkit** タブ：Maya の UV Toolkit と同じセクション構成
* **UV エディターのヘッダー**：Checker / Distortion / Texture Borders / Isolate のボタン
* **3D ビュー > サイドバー > Maya > UV**：**UV Shell Select**（3D ビューで UV シェル単位に選択）・UV エディターを開く・チェッカー・カット・Auto Seams・投影

## 3D ビューで UV シェル単位に選択（UV Shell Select）

3D ビューのサイドバー > Maya > UV の **UV Shell Select** をオンにすると、Maya の UV Shell コンポーネントのように、面を 1 つ選ぶとその UV シェル全体が選ばれます。

* クリック・Shift + クリック・ボックス・投げ縄など、どの選択方法でも効きます。
* Shift + クリックや Ctrl + ドラッグで面を外すと、そのシェル全体が外れます。
* オンの間は面選択モードになり、オフにすると元の選択モードに戻ります。
* 選択アドオン（Maya Style Select）の Symmetry と一緒に使うと、反対側のシェルも選ばれます。

## マウス操作（UV エディター）

| 操作 | 内容 |
|---|---|
| ダブルクリック | UV シェルを選択 |
| Shift + ダブルクリック | 直前に選んだ要素からの最短経路を選択 |
| 右クリック（長押しでマーキングメニュー） | コンポーネント切り替え：UV / Edge / Face / UV Shell、全選択、反転、Isolate、Sync |
| Shift + 右クリック | UV ツール：Cut / Sew / Move and Sew / Unfold / Optimize / Layout / Straighten / Orient |
| Ctrl + 右クリック | 選択の変換：To UVs / Edges / Faces / Shell / Shell Border、Grow / Shrink、Texture Borders |

マーキングメニューは左クリック選択のキーマップのときだけ有効です。Blender の右クリックメニューに戻したいときは、プリファレンスのアドオン設定で **Marking Menus** をオフにします。

## UV Toolkit の中身

| セクション | 中身 |
|---|---|
| (上部) | コンポーネント切り替え（UV / Edge / Face / Shell）、Checker、Distortion、Borders（シェル境界を太線表示）、Isolate、Sync |
| Pin | Pin / Unpin / Invert / Unpin All / Select Pinned |
| Select | Convert（UV / Edge / Face / Shell / Shell Border）、Select by Type（Texture Borders / Overlapping / Back-Facing / Front-Facing / Unmapped / Similar）、Grow / Shrink / Invert / All、Soft Select（減衰の種類も） |
| Tools | Select / Move / Rotate / Scale / Cut（Rip）/ Grab / Unfold（Relax ブラシ）/ Pinch |
| Transform | Pivot（Selection / Shell / UV Origin / 2D Cursor）、矢印で移動、回転、拡大縮小（UV / U / V）、Flip、Align（Shell ピボットならシェル単位）、Snap to Tile（3×3）、Straighten UVs / U / V、Orient Shells / Orient to Edge、Match UVs、Snap to Pixels、Randomize、Normalize、Unitize |
| Create | Planar X / Y / Z / Best、Camera-based、Cylindrical、Spherical、Cube、Automatic |
| Cut and Sew | Cut、Sew、Move and Sew、Split、Create Shell、Create Shell (Grid)、Seams from Shells、Clear Seams、Auto Seams（角度指定） |
| Unfold | **Unfold**（シェルの位置・大きさ・向きを保ったまま展開。U だけ / V だけも可）、Optimize、Straighten Shell、Unfold Brush |
| Arrange and Layout | Layout（余白・回転・拡大・形状）、Distribute U / V（間隔指定可）、Stack、Stack & Orient、Stack Similar、Unstack、Gather Shells、Normalize Scale |
| Measure | Map Size、Texel Density の Get / Set、選択の計測（シェル数、UV 面積、3D 面積、テクセル密度、2 点間の距離と角度、辺の長さ） |
| UV Sets | UV セットの一覧・追加・削除、Copy UVs to UV Set、UV Snapshot |
| Display | チェッカーの種類と解像度、歪み表示の種類、境界線の太さ、Shaded UVs |

## インストール

* Blender 4.2 以降: `blender_maya_uv.zip` を `編集 > プリファレンス > アドオン > ▼ > ディスクからインストール` で選択
* Blender 3.x〜4.1: 同じ zip を `プリファレンス > アドオン > インストール` で選択して有効化

## 注意

* Unfold は、Maya と同じくシェル単位で展開するため、実行前にシェルの境界をシームとして付けます。
* Checker はビューレイヤーのマテリアル上書きを使います。表示中はすべてのオブジェクトがチェッカーになります（もう一度押すと元に戻ります）。
* Orient to Edge は Blender に該当機能がある版（4.2 以降）でだけ表示されます。古い版の Orient Shells は簡易版の計算になります。
* Auto Seams は角度で切るため、なめらかな筒状の形などはシームが足りないことがあります。
