# A_life

人工生命・成長系の作品とツールをまとめたリポジトリです。

## フォルダ構成
```
A_life/
├─ blender/
│  ├─ vine_grow/     つる植物ジェネレータ（密度をペイントして、体に絡みつく茎と葉を生成）
│  ├─ slime_net/     粘菌ネットワーク（起点から表面に沿って網目を広げる）
│  ├─ water_flower/  成長する花（花びらを脈と膜の成長から計算する試作）
│  └─ old/           以前の Blender アドオン
│     ├─ vine_dress/   つる植物ドレス（人物に纏わせる・水中スカート・ノードグラフ版）
│     └─ vine_wrap/    つる植物ラップ（ガイドカーブで絡ませる・リアルな葉）
└─ web_alife/        以前作ったブラウザ版の人工生命作品（HTML）
```

## blender/vine_grow — つる植物ジェネレータ
体に打った点に密度をペイントして、体に絡みつきながら少し空間にも広がる、つる植物（茎と葉）を生やす Blender アドオン。
インストールは `blender/vine_grow/vine_grow.zip`。詳しくは [blender/vine_grow/README.md](blender/vine_grow/README.md)。

## blender/slime_net — 粘菌ネットワーク
オブジェクトの一点から、表面に沿って粘菌のようなネットワークを広げる Blender アドオン。
インストールは `blender/slime_net/slime_net.zip`。詳しくは [blender/slime_net/README.md](blender/slime_net/README.md)。

## blender/water_flower — 成長する花（試作）
花びらを数式で描かず、脈・二層の膜・成長・力学から形を計算して作る Blender アドオン。いまはスイセンの外花被片と副花冠。
インストールは `blender/water_flower/water_flower.zip`。詳しくは [blender/water_flower/README.md](blender/water_flower/README.md)。

## web_alife — ブラウザ版の作品
`web_alife/index.html` が作品一覧のページです（ブラウザで開くだけで動きます）。`alife_site.zip` は同じ作品一式の zip です。

| ファイル | 作品 |
|---|---|
| ca_explorer.html | CA Explorer |
| coral_bleaching.html | Coral Bleaching |
| creature_ecosystem.html | Creature Ecosystem |
| crystal_growth.html | Crystal Growth |
| current_drift.html | Current Drift |
| edge_boid_morph.html | Edge Boid Morph |
| life.html | Game of Life |
| life3d.html | Game of Life 3D |
| life_organism.html | Organism |
| life_video.html | Life × Video |
| living_morph.html | Living Morph |
| metaball_morph.html | Metaball Morph |
| mycelium.html | Mycelium |
| particle_life.html | Particle Life — Colony |
| rd_image.html | Reaction Diffusion on Image |
| rd_morph.html | RD Morph |
| reef_growth.html | Reef Growth |
| sand.html | Sand |
| slime_image.html | Slime on Image |
| slime_mold.html | Slime Mold Network |
| slime_morph.html | Slime Morph |
| vine_draw.html | Vine Draw |
| vine_growth_morph.html | Vine Growth Morph |
| voronoi_morph.html | Voronoi Morph |
