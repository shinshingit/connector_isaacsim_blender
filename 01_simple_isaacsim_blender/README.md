# Isaac Sim ⇄ Blender USD 往復ワークフロー

Isaac Sim 上での編集（位置・回転・スケール・表示/非表示など）を、Blender 側の既存オブジェクトへ「その場で」反映させるための構成とスクリプトです。リアルタイム同期ではなく、Isaac Sim で保存 → Blender でスクリプト実行、という手動の往復を想定しています。

検証環境：Blender 4.5 LTS（bpy モジュール）+ usd-core 26.8。2026年9月時点の情報に基づきます。

## 同梱ファイル

| ファイル | 実行場所 | 役割 |
|---|---|---|
| `isaacsim_setup_layer.py` | Isaac Sim（Script Editor） | 編集用の差分レイヤ（root.usda）を作成し、単位・上方向を設定してから開く |
| `usd_pull_back.py` | Blender（Scripting タブ） | root.usda を読み、Blender の既存オブジェクトを名前照合してその場で更新する |

## 全体の構成

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

のような差分だけが書かれ、base.usdc 自体は変更されません。Blender は root.usda を読むことで合成後（＝編集後）の状態を取得します。

## 手順

### 1. Blender → USD エクスポート（一度だけ）

Blender の `File > Export > Universal Scene Description (.usdc)` で `base.usdc` として書き出します。以後このファイルは Isaac Sim 側からは変更しません。

### 2. Isaac Sim 側でレイヤ構成を作る

`isaacsim_setup_layer.py` を Isaac Sim の `Window > Script Editor` に貼り付け、冒頭の2つのパスを書き換えて実行します。

```python
BASE_USD = r"C:/work/blender_out/base.usdc"
ROOT_USD = r"C:/work/blender_out/root.usda"
```

実行すると `root.usda` が新規作成され、`base.usdc` を subLayer として重ねた状態で Isaac Sim に開かれます。これ以降 GUI で行う編集（移動・回転・スケール・非表示・物理属性の付与など）は自動的に `root.usda` 側へ差分として書き込まれます。

編集後は通常どおり `Ctrl+S` で保存してください（保存先は root.usda になります）。

### 3. Blender 側の準備（一度だけ）

Blender 同梱の Python に `usd-core` をインストールします。Blender 内蔵の USD ライブラリとは独立した環境なので競合しません。

```bash
# Linux / macOS
<Blenderのフォルダ>/4.5/python/bin/python3.11 -m pip install usd-core

# Windows
"<Blenderのフォルダ>\4.5\python\bin\python.exe" -m pip install usd-core
```

### 4. Blender 側で編集を反映

`usd_pull_back.py` を Blender の `Scripting` タブに貼り付け、冒頭の `STAGE_PATH` を root.usda のパスに書き換えて実行します。

```python
STAGE_PATH = r"C:/work/blender_out/root.usda"
```

実行すると、Isaac Sim 側で編集された prim と同じ名前の Blender オブジェクトが検索され、その `matrix_world`（位置・回転・スケール）と表示/非表示がその場で更新されます。新しいオブジェクトが作られるわけではないので、マテリアル・モディファイア・他のオブジェクトはそのまま残ります。

コンソールには次のようなログが出ます。

```
[usd_pull_back] metersPerUnit=1.0 upAxis=Z
[usd_pull_back] 更新 2 件: ['Cube', 'Sphere']
```

## スクリプトの設定項目

`usd_pull_back.py` 冒頭のフラグで反映内容を調整できます。

```python
SYNC_TRANSFORMS  = True    # 位置・回転・スケールを反映
SYNC_VISIBILITY  = True    # 表示／非表示を反映
SYNC_MESH_POINTS = False   # 頂点数が一致する場合のみ頂点座標も反映

FORCE_METERS_PER_UNIT = None   # stage メタデータを信用せず手動指定したい場合（例: 1.0）
FORCE_UP_AXIS         = None   # 同上（例: "Z" or "Y"）
```

`SYNC_MESH_POINTS` は Isaac Sim 側で頂点を直接動かした場合向けのオプションですが、頂点数が変わると照合できず自動的にスキップされます。トポロジ変更を伴う編集には対応していません。

## 名前照合の仕組み

Blender の USD エクスポータは、各オブジェクトに `userProperties:blender:object_name` というカスタム属性を自動で埋め込みます。`usd_pull_back.py` はこの属性を読み、Blender 側の `bpy.data.objects[名前]` と突き合わせています。そのため：

- Blender 側でオブジェクト名を変更すると照合に失敗します（`Blender 側に該当なし` と表示）
- Isaac Sim 側で prim を rename した場合も、この属性が残っていれば追跡できます
- 上記属性を持たない新規 prim（Isaac Sim 側で新たに追加したオブジェクトなど）は「新規追加」として一覧表示されるだけで、自動インポートはされません（意図的な仕様）

## 重要な注意点（ハマりどころ）

**単位とアップ軸は subLayer から継承されません。** `metersPerUnit` と `upAxis` は stage のルートレイヤのメタデータとして解決されるため、`root.usda` に明記しないと USD のデフォルト値（`metersPerUnit = 0.01` / `upAxis = "Y"`）が使われてしまいます。この場合 Blender 側では **1/100 スケール＋X軸90度回転** という形で誤って反映されます。`isaacsim_setup_layer.py` はこれを自動で設定しますが、手動で root.usda を作る場合は必ず

```usda
(
    metersPerUnit = 1
    upAxis = "Z"
    subLayers = [@./base.usdc@]
)
```

を明記してください。

**Blender の USD インポーターは新規オブジェクトを作ります。** 通常の `File > Import > USD` を使うと、既存の Cube とは別に「Cube」というオブジェクトが増えてしまいます（同名でも別オブジェクトとして扱われる）。既存オブジェクトを更新したい場合は必ず `usd_pull_back.py` を使ってください。

## 往復できるもの・できないもの

| 項目 | 往復可否 | 備考 |
|---|---|---|
| 位置・回転・スケール | ○ | 検証済み |
| 表示／非表示 | ○ | 検証済み |
| 階層・オブジェクト名 | ○ | `userProperties:blender:object_name` で照合 |
| 頂点座標（トポロジ変更なし） | △ | `SYNC_MESH_POINTS = True` で対応、頂点数不一致時は自動スキップ |
| マテリアル | × | Blender の USD exporter が完全なマテリアル定義を書き出さないため |
| スケルタルアニメーション | × | Blender の USD I/O が未対応 |
| USD variants / レイヤ構造そのもの | × | Blender importer は合成結果のみを読み、レイヤ構造は保持しない |
| ロボットの関節（PhysX articulation） | × | Blender の USD 入出力の対象外。URDF 経由での運用を推奨 |

## もっと簡単な代替案

Blender 側で追加の作業（マテリアル調整など）をしておらず、単純に Isaac Sim の編集結果を取り込むだけでよい場合は、このワークフローを使わず、Blender で既存オブジェクトを削除してから `root.usda` を通常どおり `File > Import > USD` で読み込むだけでも成立します。スクリプトなしで完結する分、こちらのほうが手軽です。上記の仕組みが必要になるのは「Blender 側の作業（マテリアル・モディファイア・階層など）を保持したまま Isaac Sim の編集だけを反映したい」場合です。

## 参考リンク

- [Universal Scene Description — Blender Manual](https://docs.blender.org/manual/en/latest/files/import_export/usd.html)
- [Blender 5.2 Pipeline & I/O release notes](https://developer.blender.org/docs/release_notes/5.2/pipeline_io/)
- [Library Overrides and USD — Blender Developer Documentation](https://developer.blender.org/docs/features/core/overrides/library/usd_mapping/)
- [OpenUSD Fundamentals — Isaac Sim Documentation](https://docs.isaacsim.omniverse.nvidia.com/6.0.0/omniverse_usd/open_usd.html)
- [NickTiny/usd_connector](https://github.com/NickTiny/usd_connector)（同種のアドオン実装。作者曰く proof of concept、本番利用不可）
