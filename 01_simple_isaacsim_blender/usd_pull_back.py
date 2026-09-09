"""
usd_pull_back.py -- Isaac Sim (USD) の編集を Blender に反映するスクリプト

Blender の Scripting タブに貼り付け、STAGE_PATH を書き換えて実行してください。

■ 何をするか
  Isaac Sim が保存した USD（オーバーライドレイヤ）を読み、Blender の
  「既存オブジェクトをその場で更新」します。再インポートではないので
  オブジェクトが重複せず、マテリアル・モディファイア・リグ・
  他のオブジェクトはすべてそのまま残ります。

■ 事前準備（1回だけ）
  Blender に同梱の Python へ usd-core を入れます:
    Linux/macOS:
      <Blenderのフォルダ>/4.5/python/bin/python3.11 -m pip install usd-core
    Windows:
      "<Blenderのフォルダ>\\4.5\\python\\bin\\python.exe" -m pip install usd-core

■ 前提となるファイル構成
  base.usdc   ... Blender からエクスポートした元データ（触らない）
  root.usda   ... Isaac Sim 側の編集結果。base.usdc を subLayer または
                  reference し、変更点だけを "over" で持つ。
                  ★ルートレイヤに metersPerUnit と upAxis を必ず明記すること
                    （sublayer 側の値は stage メタデータとして継承されません）
"""

# ===== 設定 =====================================================
STAGE_PATH = r"/path/to/root.usda"   # Isaac Sim が保存したルート USD

SYNC_TRANSFORMS  = True    # 位置・回転・スケールを反映
SYNC_VISIBILITY  = True    # 表示／非表示を反映
SYNC_MESH_POINTS = False   # 頂点数が一致する場合のみ頂点座標も反映

# stage メタデータを信用せず手動で指定したい場合に使う（None なら自動）
FORCE_METERS_PER_UNIT = None   # 例: 1.0
FORCE_UP_AXIS         = None   # 例: "Z" または "Y"
# ================================================================

import math
import bpy
import mathutils
from pxr import Usd, UsdGeom

BLENDER_NAME_ATTR = "userProperties:blender:object_name"


def conversion_matrix(stage):
    """USD stage の単位・上方向を Blender の座標系へ変換する 4x4 行列。"""
    mpu = FORCE_METERS_PER_UNIT
    if mpu is None:
        mpu = UsdGeom.GetStageMetersPerUnit(stage)
    up = FORCE_UP_AXIS
    if up is None:
        up = str(UsdGeom.GetStageUpAxis(stage))

    scene_scale = bpy.context.scene.unit_settings.scale_length or 1.0
    mat = mathutils.Matrix.Scale(mpu / scene_scale, 4)
    if up.upper().startswith("Y"):
        mat = mathutils.Matrix.Rotation(math.radians(90.0), 4, "X") @ mat
    print(f"[usd_pull_back] metersPerUnit={mpu} upAxis={up}")
    return mat


def is_blender_generated_root(prim):
    """Blender のエクスポータが自動生成した /root を除外する。"""
    cd = prim.GetCustomData() or {}
    return bool(cd.get("Blender", {}).get("generated", False))


def object_key(prim):
    """この prim に対応する Blender のオブジェクト名。"""
    attr = prim.GetAttribute(BLENDER_NAME_ATTR)
    if attr and attr.HasAuthoredValue():
        return attr.Get()
    return prim.GetName()


def main():
    stage = Usd.Stage.Open(STAGE_PATH)
    if stage is None:
        raise RuntimeError(f"USD を開けませんでした: {STAGE_PATH}")

    conv = conversion_matrix(stage)
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    updated, unmatched, added = [], [], []

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Xformable) or is_blender_generated_root(prim):
            continue
        # Mesh 本体ではなく、その親の Xform が Blender のオブジェクトに対応する
        if prim.IsA(UsdGeom.Mesh) and prim.GetParent().IsA(UsdGeom.Xformable):
            continue

        key = object_key(prim)
        obj = bpy.data.objects.get(key)
        if obj is None:
            attr = prim.GetAttribute(BLENDER_NAME_ATTR)
            (unmatched if attr and attr.HasAuthoredValue() else added).append(
                str(prim.GetPath()))
            continue

        if SYNC_TRANSFORMS:
            m = cache.GetLocalToWorldTransform(prim)
            obj.matrix_world = conv @ mathutils.Matrix(
                [[m[c][r] for c in range(4)] for r in range(4)])

        if SYNC_VISIBILITY:
            hidden = (UsdGeom.Imageable(prim)
                      .ComputeVisibility(Usd.TimeCode.Default())
                      == UsdGeom.Tokens.invisible)
            obj.hide_viewport = hidden
            obj.hide_render = hidden

        if SYNC_MESH_POINTS and obj.type == "MESH":
            mesh_prim = next((c for c in prim.GetChildren()
                              if c.IsA(UsdGeom.Mesh)), None)
            if mesh_prim is not None:
                pts = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
                if pts and len(pts) == len(obj.data.vertices):
                    for v, p in zip(obj.data.vertices, pts):
                        v.co = (p[0], p[1], p[2])
                    obj.data.update()
                elif pts:
                    print(f"[usd_pull_back] 頂点数不一致のためスキップ: {key} "
                          f"(USD {len(pts)} / Blender {len(obj.data.vertices)})")

        updated.append(obj.name)

    print(f"[usd_pull_back] 更新 {len(updated)} 件: {updated}")
    if unmatched:
        print(f"[usd_pull_back] Blender 側に該当なし（名前変更？）: {unmatched}")
    if added:
        print(f"[usd_pull_back] Isaac Sim 側で新規追加（手動で取込が必要）: {added}")


main()
