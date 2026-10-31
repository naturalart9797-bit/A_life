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

## UV のシンメトリ選択

UV Toolkit > Select の **Symmetry** で、片側を選ぶと反対側も選ばれます（解除も同じ）。UV / 辺 / 面のどのモードでも、同期選択のオン・オフどちらでも使えます。

* **X / Y / Z**：3D でオブジェクトの軸に対して対称な位置の UV を選択（左腕のシェルを選ぶと右腕のシェルも選ばれる）
* **U / V**：UV 空間で Center（既定 0.5）の線に対して対称な位置の UV を選択

## コンポーネントの切り替え（Maya 式）

Maya と同じく、UV / Edge / Face に切り替えると **シェル選択モードを抜けて、選択が空の状態から始まります**。

* ヘッダーのボタン、**1 / 2 / 3 キー**、右クリックのマーキングメニュー、UV Toolkit 上部のボタンのどれでも同じです。
* 辺モードのままシェル選択になっていても、2 キーで確実に抜けられます（Blender 標準では同じモードのキーを押しても何も起きません）。
* ヘッダーの Island ボタンをオフにしたときも選択が解除されます。
* 3D ビューの UV Shell Select も、頂点 / 辺モードに切り替えると自動でオフになり、選択が解除されます。
* 今の選択を新しいモードに引き継ぎたいときは **Ctrl + 右クリック > Convert**（To UVs / Edges / Faces）を使います。
* Blender の動き（選択を変換して引き継ぐ）に戻したいときは、アドオン設定の **Maya Component Switching** をオフにします。
* 同期選択（Sync）がオンのときは、シェル選択から抜けるときだけ選択を解除します（3D の選択を勝手に消さないため）。

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
| Select | **Symmetry**（Off / X / Y / Z / U / V）、Convert（UV / Edge / Face / Shell / Shell Border）、Select by Type（Texture Borders / Overlapping / Back-Facing / Front-Facing / Unmapped / Similar）、Grow / Shrink / Invert / All、Soft Select（減衰の種類も） |
| Tools | Select / Move / Rotate / Scale / Cut（Rip）/ Grab / Unfold（Relax ブラシ）/ Pinch |
| Transform | Pivot（Selection / Shell / UV Origin / 2D Cursor）、矢印で移動、回転、拡大縮小（UV / U / V）、Flip、Align（Shell ピボットならシェル単位）、Snap to Tile（3×3）、Straighten UVs / U / V（角度の許容値つき）、**Edge Horizontal / Edge Vertical**（選んだ辺が水平 / 垂直になるようシェルを回転）、Orient Shells、Match UVs、Snap to Pixels、Randomize、Normalize、Unitize |
| Create | Planar X / Y / Z / Best、Camera-based、Cylindrical、Spherical、Cube、Automatic |
| Cut and Sew | Cut、**Sew**（選んだ辺を、3D で同じ辺になっている相手シェルの辺と中間点でつなぐ。片側を選ぶだけでよい）、**Move and Sew**（小さいほうのシェルを形を保ったまま回転・移動・拡大縮小して相手に合わせてからつなぐ）、Split、Create Shell、Create Shell (Grid)、Seams from Shells、Clear Seams、Auto Seams（角度指定） |
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
* Orient Shells は Blender 4.2 以降の機能を使います。古い版では簡易版の計算になります。
* Auto Seams は角度で切るため、なめらかな筒状の形などはシームが足りないことがあります。
