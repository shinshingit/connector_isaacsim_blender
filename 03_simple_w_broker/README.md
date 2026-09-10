# Isaac Sim ⇄ Blender USD 自動同期（Broker 構成）

**上書き保存をトリガーに、双方向で自動同期する**構成です。

```
Blender で Ctrl+S    →  base.usdc を自動エクスポート  →  Isaac Sim が自動リロード
Isaac Sim で Ctrl+S  →  root.usda に差分を保存        →  Blender が自動反映
```

リアルタイム同期ではなく、保存操作が同期の区切りになります。検証環境：Blender 4.5 LTS + usd-core 26.8 / Python 3.11。2026年9月時点の情報に基づきます。

## 全体構成

```
        Blender                    broker.py (venv 常駐)          Isaac Sim
   ┌──────────────┐            ┌──────────────────┐        ┌──────────────┐
   │ usd_pull_    │  HTTP/JSON │                  │        │ isaacsim_    │
   │ back.py      │◀──────────▶│  usd-core で     │        │ auto_reload  │
   │ (標準ライブラリ)│            │  root.usda を読む │        │ .py          │
   └──────┬───────┘            └────────▲─────────┘        └──────┬───────┘
          │ 保存時に自動export             │ 読む                    │ 保存時に自動reload
          ▼                              │                        ▼
     base.usdc  ◀───── subLayer ───── root.usda ◀──── Ctrl+S ─────┘
     (Blender が書く)                  (Isaac Sim が書く)
```

## なぜ Broker を挟むのか

usd-core を Blender 同梱 Python に直接インストールすると、**usd-core が同梱する `tbb` と Blender が起動時にロード済みの `tbb` が同名で衝突し、`from pxr import ...` の時点でエラーになります**（特に Windows）。DLL 検索順の操作や tbb の差し替えで回避する手もありますが、Blender のバージョンアップで簡単に壊れるため実用的ではありません。

そこで **USD を読む処理を Blender プロセスから完全に切り離し**、独立した仮想環境で常駐サーバとして動かします。Blender 側は標準ライブラリの `urllib` と `json` だけを使うため、**Blender には一切の追加パッケージを入れません**。

## ★ 運用ルール：役割分担（最重要）

**Blender と Isaac Sim で担当を分けてください。** これを守らないと編集が黙って消えます。

| | 担当すること | 担当しないこと |
|---|---|---|
| **Blender** | 形状（メッシュ）、マテリアル、オブジェクトの追加・削除・命名 | オブジェクトの配置（位置・回転・スケール） |
| **Isaac Sim** | 配置（位置・回転・スケール）、物理属性、コリジョン | 形状の編集 |

### なぜこのルールが必要か

USD の `xformOpOrder` は `uniform token[]` で、**配列が要素ごとにマージされず、強いレイヤの値で丸ごと置き換わります**。Isaac Sim がスケールだけを編集すると root.usda に `xformOpOrder = ["xformOp:scale"]` が書かれ、base.usdc 側の translate は「存在するのに適用されない」状態になります。

実測例：

```
合成後の xformOpOrder = [xformOp:scale]
    root.usda  -> [xformOp:scale]                                    ← 勝つ
    base.usdc  -> [xformOp:translate, xformOp:rotateXYZ, xformOp:scale]

xformOp:translate = (9, 0, 0)   ← base.usdc に確かに存在する
合成後の translate = (0, 0, 0)  ← しかし適用されない
```

これは USD の仕様どおりの動作で、**Isaac Sim のビューポートでも同じように原点に戻って見えます**。つまり Blender 側で位置決めをしても、その prim を Isaac Sim が一度でも動かしていれば無視されます。

### 違反すると警告が出ます

黙って消えるのを防ぐため、broker と Blender の両方が検知して警告します。

```
[broker] ★ 役割分担ルール違反: /root/Cube
          Blender 側の xformOp:translate=[9.0, 0.0, 0.0] が Isaac Sim の transform 編集に打ち消されています
```

この警告が出たら、そのオブジェクトの配置は Isaac Sim 側で行ってください。Blender 側では原点に戻しておくのが安全です。

## 同梱ファイル

| ファイル | 実行場所 | 依存 | 役割 |
|---|---|---|---|
| `isaacsim_setup_layer.py` | Isaac Sim（Script Editor、初回のみ） | Isaac Sim 内蔵の pxr | 差分レイヤ root.usda を作成し、単位・上方向を設定して開く |
| `isaacsim_auto_reload.py` | Isaac Sim（Script Editor、常駐） | Isaac Sim 内蔵の pxr | base.usdc の更新を検知して自動リロード |
| `broker.py` | 専用の venv（常駐） | usd-core | root.usda を読み、JSON に変換して HTTP で配信 |
| `usd_pull_back.py` | Blender（Scripting タブ、常駐） | **なし（標準ライブラリのみ）** | 保存時の自動エクスポートと、Isaac Sim 保存の自動反映 |
| `diagnose.py` | 専用の venv（単発） | usd-core | 「反映されない」ときの原因切り分け |

## 誰がどのファイルを更新するのか

**各ファイルには書き手が1人しかいません。**

| ファイル | 書き手 | 読み手 | 書かれるタイミング |
|---|---|---|---|
| `base.usdc` | **Blender のみ** | Isaac Sim / broker | Blender で `Ctrl+S`（自動エクスポート） |
| `root.usda` | **Isaac Sim のみ** | broker | Isaac Sim で `Ctrl+S` |
| `.blend` | Blender のみ | — | Blender で `Ctrl+S` |

broker はどちらにも書き込みません（読むだけ）。`usd_pull_back.py` も USD には書き戻しません。

## セットアップ

### 1. Blender → USD の初回エクスポート

Blender の `File > Export > Universal Scene Description (.usdc)` で `base.usdc` として書き出します。2回目以降は保存時に自動化されるので、手動操作はこの1回だけです。

### 2. Isaac Sim 側でレイヤ構成を作る（初回のみ）

`isaacsim_setup_layer.py` を Isaac Sim の `Window > Script Editor` に貼り付け、パス2行を書き換えて実行します。

```python
BASE_USD = r"C:/work/blender_out/base.usdc"
ROOT_USD = r"C:/work/blender_out/root.usda"
```

`root.usda` が作られ、`base.usdc` を subLayer として重ねた状態で開かれます。

### 3. Broker 用の仮想環境を作る（初回のみ）

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

起動時に `metersPerUnit=1.0 upAxis=Z` 以外が表示されたら警告が出ます。

### 5. Isaac Sim 側の常駐を開始する

`isaacsim_auto_reload.py` を Script Editor に貼り付け、`BASE_USD` を書き換えて実行します。以降、Blender が `base.usdc` を更新するたびに自動でリロードされます。

### 6. Blender 側の常駐を開始する

`usd_pull_back.py` を `Scripting` タブに貼り付け、`BASE_USD` を書き換えて実行します。

```python
MODE = "auto"
BROKER_URL = "http://127.0.0.1:8765"
BASE_USD = r"C:/work/blender_out/base.usdc"
```

これで双方向の自動同期が始まります。以降は普通に `Ctrl+S` するだけです。

## 常駐の停止

どちらのスクリプトも `MODE = "stop"` にして再実行すると解除されます。アプリケーションを再起動しても止まります（常駐状態はファイルに保存されません）。

## Blender 側スクリプトの設定項目

```python
MODE = "auto"              # "auto" 常駐 / "once" 1回だけ反映 / "stop" 解除

AUTO_EXPORT_ON_SAVE = True # Ctrl+S で base.usdc を自動エクスポート
BASE_USD = r"..."
EXPORT_OPTIONS = {         # bpy.ops.wm.usd_export に渡す引数
    "selected_objects_only": False,
    "export_animation": False,
    "export_materials": True,
}

AUTO_PULL = True           # Isaac Sim の保存を検知して自動反映
POLL_INTERVAL_SEC = 2.0

SYNC_TRANSFORMS  = True
SYNC_VISIBILITY  = True
SYNC_MESH_POINTS = False
```

自動エクスポートは保存のたびに走ります。シーンが重くて保存が遅くなる場合は `AUTO_EXPORT_ON_SAVE = False` にして、必要なときだけ手動エクスポートしてください。

## Broker の HTTP API

| エンドポイント | 内容 |
|---|---|
| `GET /health` | 稼働確認。USD / Python のバージョンと stage パス |
| `GET /stage` | メタデータのみ。`root_mtime` で Isaac Sim の保存を検知する |
| `GET /scene` | シーン全体のスナップショット |
| `GET /scene?reload=1` | 再読み込みを強制 |
| `GET /scene?points=1` | メッシュ頂点座標も含める |

`/scene` のレスポンス：

```json
{
  "schema": 1,
  "stage": { "metersPerUnit": 1.0, "upAxis": "Z", "root_mtime": 1788941480.2 },
  "objects": [
    { "prim_path": "/root/Cube", "blender_name": "Cube", "from_blender": true,
      "matrix_world": [[...], [...], [...], [...]], "visible": true }
  ],
  "conflicts": []
}
```

`matrix_world` は **メートル・Z-up に換算済み**、かつ Blender の `mathutils.Matrix()` にそのまま渡せる行優先（平行移動が最終列）形式です。USD の `Gf.Matrix4d` は行ベクトル規約なので broker 内で転置しています。

`root_mtime` は root.usda 単体の更新時刻です。Blender の自動エクスポートで base.usdc が変わっても動かないので、「Isaac Sim が保存したか」だけを正確に検知できます。

### 起動オプション

```
--stage PATH              Isaac Sim が保存したルート USD（必須）
--host ADDR               待ち受けアドレス（既定 127.0.0.1）
--port N                  ポート（既定 8765）
--token STR               共有トークン。指定すると X-Auth-Token ヘッダを要求
--meters-per-unit FLOAT   単位を強制（例: 1.0）
--up-axis Z|Y             上方向を強制
--verbose                 アクセスログを出す
```

既定では `127.0.0.1` にのみバインドし、`Origin` ヘッダ付きのリクエスト（ブラウザ経由）は拒否します。

## 名前照合の仕組み

Blender の USD エクスポータは、各オブジェクトに `userProperties:blender:object_name` を自動で埋め込みます。broker はこれを `blender_name` として返し、Blender 側が `bpy.data.objects[名前]` と突き合わせます。

Blender 側でオブジェクト名を変更すると照合に失敗しますが、保存時に自動エクスポートが走るため base.usdc 側の prim 名も追随します。ただし **root.usda の `over` は古い prim パスを指したまま残る**ので、そのオブジェクトに対する Isaac Sim の編集は失われます。名前変更は Isaac Sim 側の編集前に済ませてください。

## 検証済みの動作

コンテナ内で Blender 4.5 LTS と usd-core 26.8 を使い、`pxr` の import を強制的にブロックした Blender プロセスで確認しています。

- 移動・回転・スケール・可視性が正しく反映される
- Blender 側のマテリアル・モディファイア・他オブジェクトが無傷で残る
- オブジェクトの重複が発生しない
- Blender の `Ctrl+S` で base.usdc が自動エクスポートされる
- Isaac Sim の保存（root.usda の更新）を検知して自動反映される
- 常駐解除後は自動エクスポートも自動反映も止まる
- broker を起動したまま USD を書き換えても自動的に新しい値を返す
- Y-up・センチメートル単位の stage でもメートル・Z-up に正しく換算される
- 役割分担ルール違反を検知して警告する（誤検知なし）
- Isaac Sim が単一行列（`xformOp:transform`）で書いた場合も正しく処理する

## 重要な注意点

**Blender 同梱 Python に usd-core を入れてはいけません。** tbb 衝突でこの構成の前提が崩れます。すでに入れてしまった場合はアンインストールしてください。

**単位とアップ軸は subLayer から継承されません。** `metersPerUnit` と `upAxis` は stage のルートレイヤのメタデータとして解決されるため、`root.usda` に明記しないと USD のデフォルト（`0.01` / `Y`）が使われ、Blender 側で 1/100 スケール＋X軸90度回転になります。`isaacsim_setup_layer.py` が自動設定しますが、手動で作る場合は必ず明記してください。

**USD はレイヤをプロセス内でキャッシュします。** `Usd.Stage.Open()` を呼び直してもディスクの変更は反映されません。broker は `stage.Reload()` を使っています。

**プロキシ環境変数に注意。** `HTTP_PROXY` などが設定されていると `urllib` が localhost 宛でもプロキシを経由しようとします。`usd_pull_back.py` は明示的に無効化していますが、接続できない場合はこの点を確認してください。

## 往復できるもの・できないもの

| 項目 | 往復可否 | 備考 |
|---|---|---|
| 配置（位置・回転・スケール） | ○ | Isaac Sim → Blender。逆方向は役割分担ルールにより非対応 |
| 表示／非表示 | ○ | Isaac Sim → Blender |
| 形状（メッシュ） | ○ | Blender → Isaac Sim（自動エクスポート＋自動リロード） |
| オブジェクトの追加・削除 | △ | Blender → Isaac Sim は可。Isaac Sim 側の新規追加は手動取込が必要 |
| 単位・アップ軸の違い | ○ | broker が正規化 |
| 頂点座標（トポロジ変更なし） | △ | `SYNC_MESH_POINTS = True`。Y-up stage では未対応 |
| マテリアル | × | Blender の USD exporter が完全なマテリアル定義を書き出さない |
| スケルタルアニメーション | × | Blender の USD I/O が未対応 |
| ロボットの関節（PhysX articulation） | × | Blender の USD 入出力の対象外。URDF 経由を推奨 |

## トラブルシューティング

### まず `diagnose.py` を実行する

```bash
.venv/bin/python diagnose.py --stage /path/to/root.usda --broker http://127.0.0.1:8765
```

各 prim の値が**どのレイヤ由来か**を表示します。

```
/root/Cube   [★同期対象]
  blender_name = Cube
  xformOp:scale = (5, 5, 5)      ← root.usda    Isaac Sim の編集が効いている
  xformOp:translate = (4, 0, 0)  ← base.usdc    Blender の元の値のまま
```

### 症状別

**Isaac Sim で編集して保存したのに、何も変わらない** — `diagnose.py` が「ルートレイヤに xformOp が1つもありません」と出したら、Isaac Sim で開いているのが `root.usda` か（`base.usdc` を直接開いていないか）、Layer ウィンドウで `root.usda` が authoring layer（鉛筆マーク／太字）になっているか、`Ctrl+S` を押してタイトルバーの `*` が消えたか、を確認してください。Session Layer が編集対象だと保存されません。

**Blender で移動したのに Isaac Sim で原点に戻る** — 役割分担ルール違反です。配置は Isaac Sim 側で行ってください。詳しくは上記「運用ルール」を参照。

**Blender 側のログが「更新 0 件」** — `blender_name` と Blender のオブジェクト名が一致していません。`diagnose.py` の出力と Blender のアウトライナーを見比べてください。

**Blender の保存が遅くなった** — 自動エクスポートがシーン全体を書き出しています。`AUTO_EXPORT_ON_SAVE = False` にするか、`EXPORT_OPTIONS` で `selected_objects_only` などを調整してください。

**`broker に接続できません`** — broker が起動しているか、ポート番号が一致しているか。`curl http://127.0.0.1:8765/health` で切り分けられます。

**`pxr が見つかりません`（broker 起動時）** — venv の python で実行していません。`.venv/bin/python broker.py ...` とフルパスで指定してください。

**`Address already in use`** — 前回の broker が残っています。終了させるか `--port` で別のポートを指定してください。

**Isaac Sim 側で自動リロードされない** — `isaacsim_auto_reload.py` の `BASE_USD` が Blender 側の `BASE_USD` と同じパスを指しているか確認してください。「base.usdc が現在の stage に含まれていません」と出る場合は、`root.usda` ではなく別のファイルを開いています。

## 参考リンク

- [Universal Scene Description — Blender Manual](https://docs.blender.org/manual/en/latest/files/import_export/usd.html)
- [Blender 5.2 Pipeline & I/O release notes](https://developer.blender.org/docs/release_notes/5.2/pipeline_io/)
- [OpenUSD Fundamentals — Isaac Sim Documentation](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/omniverse_usd/open_usd.html)
- [usd-core — PyPI](https://pypi.org/project/usd-core/)
