# flower_v008 リグ

`flower_v008_rig.blend` は `flower_v008.blend` に `rig_flower_fairy.py` を実行して作ったファイルです（元ファイルはそのまま残してあります）。
モデルを修正したときは、元ファイルを開いて Scripting タブでこのスクリプトを実行すればリグを作り直せます。

骨はすべて普通の FK ボーンです。コントローラーやドライバーはありません。
ポーズモードで骨を回転・スケールして、キーを打って使います。

## fairy_rig（妖精）— 標準的な人型の骨格
原点（`root`）は、花の中にある口（mouth_geo）の底の穴です。

```
root (穴。穴より下に埋まっている部分を固定)
└ tail.001 … tail.012   蛇の部分 (穴 → 腰)
  └ hips
    └ spine └ spine.001 └ chest
                         ├ neck └ head        (目は head にペアレント)
                         └ shoulder.L/R └ upper_arm └ forearm └ hand
                                                           └ thumb / index / middle / ring / pinky (.01 .02 .03)
```

- 骨コレクション：`Root & Tail` / `Body` / `Arms` / `Fingers`
- 左右は `.L` / `.R` なので、ポーズのコピー・反転貼り付けが使えます。
- `fairy_rig` は `flower_rig` の `flower_head` にペアレントされているので、花と一緒に動きます。

## flower_rig（花）
| ボーン | 役割 |
|---|---|
| `flower_root` | 全体。地面の根はこれに固定 |
| `stalk.001`〜`005` | 胃袋より上の茎 |
| `flower_head` | 花の頭（花びら・口） |
| `stomach` | **スケールで胃袋全体が膨張（>1）・収縮（<1）** |
| `stomach_lobe.*` | front / back / L / R / top / bottom。Y スケールでその方向だけ膨らむ・へこむ |

胃袋に絡んでいる蔓は、胃袋の表面と一緒に動きます。
