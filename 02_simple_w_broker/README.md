# Isaac Sim ⇄ Blender USD 往復ワークフロー（Broker 構成）

Isaac Sim 上での編集（位置・回転・スケール・表示/非表示など）を、Blender 側の既存オブジェクトへ「その場で」反映させるための構成とスクリプトです。リアルタイム同期ではなく、Isaac Sim で保存 → Blender でスクリプト実行、という手動の往復を想定しています。

検証環境：Blender 4.5 LTS（bpy モジュール）+ usd-core 26.8 / Python 3.11。2026年9月時点の情報に基づきます。

## なぜ Broker を挟むのか

usd-core を Blender 同梱 Python に直接インストールすると、**usd-core が同梱する `tbb` と Blender が起動時にロード済みの `tbb` が同名で衝突し、`from pxr import ...` の時点でエラーになります**（特に Windows）。DLL 検索順の操作や tbb の差し替えで回避する手もありますが、Blender のバージョンアップで簡単に壊れるため実用的ではありません。

そこで **USD を読む処理を Blender プロセスから完全に切り離し**、独立した仮想環境で常駐サーバ（broker）として動かします。Blender 側は標準ライブラリの `urllib` と `json` だけを使うため、**Blender には一切の追加パッケージを入れません**。

```
Isaac Sim  ──(root.usda を保存)──▶  [ファイル]
                                       │
                                       ▼ usd-core で読む
Blender  ◀──(HTTP / JSON)──▶  broker.py  (venv, 常駐)
 └ 標準ライブラリのみ            └ pxr はここだけに存在
```

## 同梱ファイル

| ファイル | 実行場所 | 依存 | 役割 |
|---|---|---|---|
| `isaacsim_setup_layer.py` | Isaac Sim（Script Editor） | Isaac Sim 内蔵の pxr | 編集用の差分レイヤ（root.usda）を作成し、単位・上方向を設定してから開く |
| `broker.py` | 専用の venv（常駐） | usd-core | root.usda を読み、JSON に変換して HTTP で配信する |
| `usd_pull_back.py` | Blender（Scripting タブ） | **なし（標準ライブラリのみ）** | broker から受け取った情報で既存オブジェクトをその場更新する |

## USD 側の構成

```
root.usda        ← Isaac Sim が編集・保存するファイル（差分 "over" のみ）
 └ subLayer: base.usdc   ← Blender が書き出した元データ（Isaac Sim は書き換えない）
```

Isaac Sim で cube を2倍にすると、root.usda には

```usda
over "root"
{
    over "Cube"
    {
        float3 xformOp:scale = (2, 2, 2)
    }
}
```

のような差分だけが書かれ、base.usdc 自体は変更されません。broker は root.usda を開くことで合成後（＝編集後）の状態を取得します。

## セットアップと手順

### 1. Blender → USD エクスポート（一度だけ）

Blender の `File > Export > Universal Scene Description (.usdc)` で `base.usdc` として書き出します。以後このファイルは Isaac Sim 側からは変更しません。

### 2. Isaac Sim 側でレイヤ構成を作る（一度だけ）

`isaacsim_setup_layer.py` を Isaac Sim の `Window > Script Editor` に貼り付け、冒頭の2つのパスを書き換えて実行します。

```python
BASE_USD = r"C:/work/blender_out/base.usdc"
ROOT_USD = r"C:/work/blender_out/root.usda"
```

実行すると `root.usda` が新規作成され、`base.usdc` を subLayer として重ねた状態で Isaac Sim に開かれます。以降 GUI で行う編集（移動・回転・スケール・非表示・物理属性の付与など）は自動的に `root.usda` 側へ差分として書き込まれます。編集後は通常どおり `Ctrl+S` で保存してください。

### 3. Broker 用の仮想環境を作る（一度だけ）

Blender や Isaac Sim とは無関係な、素の Python で構いません。

```bash
python -m venv .venv

# Linux / macOS
.venv/bin/pip install usd-core

# Windows
.venv\Scripts\pip install usd-core
```

### 4. Broker を起動する（作業セッションごと）

別のコンソールを開いたままにしておきます。

```bash
# Linux / macOS
.venv/bin/python broker.py --stage /path/to/root.usda

# Windows
.venv\Scripts\python.exe broker.py --stage C:\work\blender_out\root.usda
```

起動時に stage を読んでメタデータを表示します。ここで `metersPerUnit=1.0 upAxis=Z` 以外が表示された場合は警告が出るので、root.usda のメタデータを確認してください。

### 5. Blender 側で編集を反映

`usd_pull_back.py` を Blender の `Scripting` タブに貼り付けて実行します。broker がデフォルトのポートで動いていれば設定変更は不要です。

```python
BROKER_URL = "http://127.0.0.1:8765"
```

コンソールには次のようなログが出ます。

```
[pull_back] broker OK (USD 0.26.8, Python 3.11.15)
[pull_back] stage: metersPerUnit=1.0 upAxis=Z
[pull_back] 更新 2 件: ['Cube', 'Sphere']
```

以降は、Isaac Sim で編集 → `Ctrl+S` → Blender でスクリプト再実行、を繰り返すだけです。**broker は起動したままで構いません。**ファイルの更新時刻を見て自動的に読み直します。

## Broker の HTTP API

| エンドポイント | 内容 |
|---|---|
| `GET /health` | 稼働確認。USD / Python のバージョンと stage パスを返す |
| `GET /stage` | stage のメタデータのみ（metersPerUnit / upAxis / 構成レイヤ / mtime） |
| `GET /scene` | シーン全体のスナップショット。Blender 側が使う本体 |
| `GET /scene?reload=1` | ファイルの再読み込みを強制する |
| `GET /scene?points=1` | メッシュ頂点座標も含める |

`/scene` のレスポンス構造：

```json
{
  "schema": 1,
  "stage": { "path": "...", "metersPerUnit": 1.0, "upAxis": "Z", "mtime": 1788941480.2 },
  "objects": [
    {
      "prim_path": "/root/Cube",
      "blender_name": "Cube",
      "from_blender": true,
      "matrix_world": [[...], [...], [...], [...]],
      "visible": true
    }
  ]
}
```

`matrix_world` は **メートル・Z-up に換算済み**、かつ Blender の `mathutils.Matrix()` にそのまま渡せる行優先（平行移動が最終列）形式です。Blender 側はシーン単位（`scale_length`）を掛けるだけで済みます。USD の `Gf.Matrix4d` は行ベクトル規約なので、broker 内で転置しています。

## Broker の起動オプション

```
--stage PATH              Isaac Sim が保存したルート USD（必須）
--host ADDR               待ち受けアドレス（既定 127.0.0.1）
--port N                  ポート（既定 8765）
--token STR               共有トークン。指定すると X-Auth-Token ヘッダを要求する
--meters-per-unit FLOAT   stage メタデータを信用せず単位を強制（例: 1.0）
--up-axis Z|Y             stage メタデータを信用せず上方向を強制
--verbose                 アクセスログを出す
```

既定では `127.0.0.1` にのみバインドし、`Origin` ヘッダが付いたリクエスト（ブラウザ経由）は拒否します。共有マシンで使う場合は `--token` を指定し、`usd_pull_back.py` の `AUTH_TOKEN` に同じ値を設定してください。

## Blender 側スクリプトの設定項目

```python
BROKER_URL = "http://127.0.0.1:8765"
AUTH_TOKEN = ""            # broker を --token 付きで起動した場合のみ設定

SYNC_TRANSFORMS  = True    # 位置・回転・スケールを反映
SYNC_VISIBILITY  = True    # 表示／非表示を反映
SYNC_MESH_POINTS = False   # 頂点数が一致する場合のみ頂点座標も反映

FORCE_RELOAD = True        # broker にファイルの再読み込みを促す
TIMEOUT_SEC  = 30.0
```

## 名前照合の仕組み

Blender の USD エクスポータは、各オブジェクトに `userProperties:blender:object_name` というカスタム属性を自動で埋め込みます。broker はこれを読んで `blender_name` として返し、Blender 側が `bpy.data.objects[名前]` と突き合わせます。そのため：

- Blender 側でオブジェクト名を変更すると照合に失敗します（`Blender 側に該当なし` と表示）
- Isaac Sim 側で prim を rename しても、この属性が残っていれば追跡できます
- この属性を持たない prim（Isaac Sim 側で新規追加したオブジェクトなど）は `from_blender: false` として一覧表示されるだけで、自動インポートはされません（意図的な仕様）

## 検証済みの動作

コンテナ内で Blender 4.5 LTS と usd-core 26.8 を使い、`pxr` の import を強制的にブロックした Blender プロセスで以下を確認しています。

- 移動 (1, 2, 3)・回転 45度・スケール 5倍が正しく反映される
- 別オブジェクトの非表示化が反映される
- Blender 側のマテリアル・モディファイア・他オブジェクトが無傷で残る
- オブジェクトの重複が発生しない
- Isaac Sim 側で付与した `RigidBodyAPI` / `CollisionAPI` は無害に無視される
- **broker を起動したまま USD を書き換えても、次のリクエストで自動的に新しい値を返す**
- Y-up・センチメートル単位の stage でも、メートル・Z-up に正しく換算される（USD `(0, 300, 0)` Y-up cm → Blender `(0, 0, 3)` m）

## 重要な注意点（ハマりどころ）

**tbb 衝突のため、Blender 同梱 Python に usd-core を入れてはいけません。** この構成の前提そのものです。すでに入れてしまった場合はアンインストールしてください。

**単位とアップ軸は subLayer から継承されません。** `metersPerUnit` と `upAxis` は stage のルートレイヤのメタデータとして解決されるため、`root.usda` に明記しないと USD のデフォルト値（`metersPerUnit = 0.01` / `upAxis = "Y"`）が使われ、Blender 側で 1/100 スケール＋X軸90度回転として反映されます。`isaacsim_setup_layer.py` はこれを自動設定しますが、手動で作る場合は必ず以下を明記してください。

```usda
(
    metersPerUnit = 1
    upAxis = "Z"
    subLayers = [@./base.usdc@]
)
```

broker は起動時とリクエスト時にこれをチェックし、想定と異なる場合は警告を出します。

**USD はレイヤをプロセス内でキャッシュします。** `Usd.Stage.Open()` を呼び直してもディスク上の変更は反映されません。broker は `stage.Reload()` を使って再読み込みしています（自前で broker を改造する場合の注意点）。

**プロキシ環境変数に注意。** `HTTP_PROXY` などが設定された環境では `urllib` が localhost 宛でもプロキシを経由しようとします。`usd_pull_back.py` は明示的にプロキシを無効化した opener を使っているので通常は問題ありませんが、接続できない場合はこの点を確認してください。

**Blender の USD インポーターは新規オブジェクトを作ります。** 通常の `File > Import > USD` を使うと、既存の Cube とは別に「Cube」というオブジェクトが増えます。既存オブジェクトを更新したい場合は必ず `usd_pull_back.py` を使ってください。

## 往復できるもの・できないもの

| 項目 | 往復可否 | 備考 |
|---|---|---|
| 位置・回転・スケール | ○ | 検証済み |
| 表示／非表示 | ○ | 検証済み |
| 階層・オブジェクト名 | ○ | `userProperties:blender:object_name` で照合 |
| 単位・アップ軸の違い | ○ | broker がメートル・Z-up に正規化。検証済み |
| 頂点座標（トポロジ変更なし） | △ | `SYNC_MESH_POINTS = True` で対応。頂点数不一致時は自動スキップ。Y-up stage では未対応 |
| マテリアル | × | Blender の USD exporter が完全なマテリアル定義を書き出さないため |
| スケルタルアニメーション | × | Blender の USD I/O が未対応 |
| USD variants / レイヤ構造そのもの | × | 合成結果のみを扱い、レイヤ構造は保持しない |
| ロボットの関節（PhysX articulation） | × | Blender の USD 入出力の対象外。URDF 経由での運用を推奨 |

## トラブルシューティング

**`broker に接続できません`** — broker が起動しているか、ポート番号が一致しているかを確認してください。`curl http://127.0.0.1:8765/health` で切り分けられます。

**`pxr が見つかりません`（broker 起動時）** — venv の python で実行していない可能性があります。`.venv/bin/python broker.py ...` のようにフルパスで指定してください。

**`Address already in use`** — 前回の broker が残っています。プロセスを終了させるか `--port` で別のポートを指定してください。

**スケールが 1/100 になる、モデルが90度倒れる** — root.usda のルートレイヤに `metersPerUnit` / `upAxis` が書かれていません。上記「重要な注意点」を参照するか、broker を `--meters-per-unit 1.0 --up-axis Z` 付きで起動して強制してください。

**`Blender 側に該当なし`** — Blender 側でオブジェクト名を変更した可能性があります。名前を戻すか、base.usdc を書き出し直してください。

## 参考リンク

- [Universal Scene Description — Blender Manual](https://docs.blender.org/manual/en/latest/files/import_export/usd.html)
- [Blender 5.2 Pipeline & I/O release notes](https://developer.blender.org/docs/release_notes/5.2/pipeline_io/)
- [Library Overrides and USD — Blender Developer Documentation](https://developer.blender.org/docs/features/core/overrides/library/usd_mapping/)
- [OpenUSD Fundamentals — Isaac Sim Documentation](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/omniverse_usd/open_usd.html)
- [usd-core — PyPI](https://pypi.org/project/usd-core/)
