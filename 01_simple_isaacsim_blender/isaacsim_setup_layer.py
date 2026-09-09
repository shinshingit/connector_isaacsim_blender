"""
isaacsim_setup_layer.py -- Isaac Sim の Script Editor で実行するセットアップ

Blender が書き出した base.usdc を「読み取り専用の元データ」として参照し、
Isaac Sim 側の編集は root.usda に "over"（差分）だけを書き込む構成を作ります。

  root.usda  <- Isaac Sim が編集・保存するファイル（差分のみ）
   └ subLayer: base.usdc  <- Blender が書き出した元データ（書き換えない）

この構成にしておくと
  ・Blender 側の元データが Isaac Sim に壊されない
  ・Isaac Sim の編集内容が root.usda に差分として溜まる
  ・Blender は root.usda を読むだけで合成後（＝編集後）の状態を得られる
という往復が成立します。

Isaac Sim の Window > Script Editor に貼り付けて、パス 2 行を書き換えて実行。
"""

BASE_USD = r"C:/work/blender_out/base.usdc"   # Blender からエクスポートしたファイル
ROOT_USD = r"C:/work/blender_out/root.usda"   # これから作る編集用ファイル

import os
import omni.usd
from pxr import Usd, UsdGeom, Sdf

# --- root.usda を作り、base.usdc を subLayer として重ねる ---
root_layer = Sdf.Layer.FindOrOpen(ROOT_USD) or Sdf.Layer.CreateNew(ROOT_USD)
rel = os.path.relpath(BASE_USD, os.path.dirname(ROOT_USD)).replace("\\", "/")
if not rel.startswith("."):
    rel = "./" + rel
if rel not in root_layer.subLayerPaths:
    root_layer.subLayerPaths.append(rel)

stage = Usd.Stage.Open(root_layer)

# ★重要: metersPerUnit と upAxis は subLayer から継承されないので
#         ルートレイヤに必ず明記する（Blender 側で 1/100 スケールになるのを防ぐ）
UsdGeom.SetStageMetersPerUnit(stage, 1.0)
UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)

root_layer.Save()
print("created:", ROOT_USD)

# --- Isaac Sim でこの root.usda を開く ---
# これ以降、GUI での編集は自動的に root.usda 側に "over" として書かれる
omni.usd.get_context().open_stage(ROOT_USD)
print("opened in Isaac Sim. 編集後は Ctrl+S で root.usda に保存してください。")
