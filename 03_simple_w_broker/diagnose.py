#!/usr/bin/env python3
"""
diagnose.py -- 「Isaac Sim で編集したのに Blender に反映されない」の切り分け

broker と同じ仮想環境（usd-core 入り）で実行してください。

    .venv/bin/python diagnose.py --stage C:/usd_bridge/root.usda

    # broker も起動中なら、broker の応答も突き合わせる
    .venv/bin/python diagnose.py --stage C:/usd_bridge/root.usda --broker http://127.0.0.1:8765

何を見るか
  1. root.usda と base.usdc が実在し、いつ更新されたか
  2. root.usda に Isaac Sim の編集（over / xformOp）が実際に書かれているか  ← 最重要
  3. 各 prim の合成後トランスフォームと、その値が「どのレイヤ由来か」
  4. Blender 側のオブジェクト名との対応（userProperties:blender:object_name）
"""

import argparse
import datetime
import json
import os
import sys
import urllib.request

try:
    from pxr import Usd, UsdGeom, Sdf
except ImportError:
    sys.exit("pxr が見つかりません。usd-core を入れた venv の python で実行してください。")

BLENDER_NAME_ATTR = "userProperties:blender:object_name"
XFORM_OPS = ("xformOp:transform", "xformOp:translate", "xformOp:orient",
             "xformOp:rotateXYZ", "xformOp:rotateXZY", "xformOp:rotateYXZ",
             "xformOp:rotateYZX", "xformOp:rotateZXY", "xformOp:rotateZYX",
             "xformOp:scale", "xformOpOrder", "visibility")


def ts(path):
    if not os.path.exists(path):
        return "(存在しません)"
    m = os.path.getmtime(path)
    return (datetime.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M:%S")
            + f"  ({os.path.getsize(path)} bytes)")


def head(title):
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)


def is_generated_root(prim):
    cd = prim.GetCustomData() or {}
    return bool(cd.get("Blender", {}).get("generated", False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True)
    ap.add_argument("--broker", default=None,
                    help="起動中の broker の URL（任意）")
    args = ap.parse_args()

    stage_path = os.path.abspath(args.stage)

    head("1. ファイルの状態")
    print(f"root  : {stage_path}")
    print(f"        {ts(stage_path)}")
    if not os.path.exists(stage_path):
        sys.exit("\n→ root.usda がありません。isaacsim_setup_layer.py を実行してください。")

    stage = Usd.Stage.Open(stage_path)
    if stage is None:
        sys.exit("→ USD として開けませんでした。")

    root_layer = stage.GetRootLayer()
    sublayers = list(root_layer.subLayerPaths)
    print(f"\nsubLayers: {sublayers if sublayers else '(なし)'}")
    if not sublayers:
        print("→ ★ subLayer が設定されていません。isaacsim_setup_layer.py が")
        print("     正しく実行されていない可能性があります。")

    for layer in stage.GetUsedLayers():
        if layer.realPath and layer.realPath != stage_path:
            print(f"\nbase  : {layer.realPath}")
            print(f"        {ts(layer.realPath)}")

    head("2. stage メタデータ")
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    up = str(UsdGeom.GetStageUpAxis(stage))
    print(f"metersPerUnit = {mpu}")
    print(f"upAxis        = {up}")
    if mpu != 1.0 or not up.upper().startswith("Z"):
        print("\n→ ★ Blender 想定（1.0 / Z）と異なります。root.usda のルートレイヤに")
        print("     metersPerUnit と upAxis を明記してください。")

    head("3. root.usda の中身（Isaac Sim の編集がここに入っているか）")
    text = root_layer.ExportToString()
    print(text if len(text) < 4000 else text[:4000] + "\n... (以下省略)")

    # ルートレイヤに prim spec があるか＝Isaac Sim の編集が保存されているか
    authored = []
    def walk(spec):
        for child in spec.nameChildren:
            for prop in child.properties:
                if prop.name in XFORM_OPS or prop.name.startswith("xformOp"):
                    authored.append(f"{child.path}.{prop.name}")
            walk(child)
    walk(root_layer.pseudoRoot)

    print()
    if authored:
        print("→ ルートレイヤに編集が書かれています:")
        for a in authored:
            print(f"     {a}")
    else:
        print("→ ★★ ルートレイヤに xformOp が1つもありません。")
        print("       Isaac Sim 側の編集が root.usda に保存されていません。")
        print()
        print("     確認すること:")
        print("       a) Isaac Sim で開いているのは root.usda か？")
        print("          （base.usdc を直接開いていないか）")
        print("       b) Layer ウィンドウで root.usda が authoring layer")
        print("          （鉛筆マーク／太字）になっているか？")
        print("          Session Layer が編集対象だと保存されません。")
        print("       c) 編集後に Ctrl+S を押したか？")
        print("          タイトルバーに未保存マーク（*）が残っていないか？")

    head("4. prim ごとの合成結果と、値の出どころ")
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    found = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Xformable):
            continue
        gen = is_generated_root(prim)
        is_mesh_child = (prim.IsA(UsdGeom.Mesh)
                         and prim.GetParent().IsA(UsdGeom.Xformable))

        attr = prim.GetAttribute(BLENDER_NAME_ATTR)
        has_name = bool(attr and attr.HasAuthoredValue())
        bname = attr.Get() if has_name else "(なし)"

        role = []
        if gen:
            role.append("Blender生成root/対象外")
        if is_mesh_child:
            role.append("Mesh本体/対象外")
        if not role:
            role.append("★同期対象")
            found += 1

        m = cache.GetLocalToWorldTransform(prim)
        trans = [round(m[3][i], 4) for i in range(3)]
        # スケールは各行の長さから概算
        scale = [round(sum(m[r][c] ** 2 for c in range(3)) ** 0.5, 4)
                 for r in range(3)]

        print(f"\n{prim.GetPath()}   [{', '.join(role)}]")
        print(f"  blender_name = {bname}")
        print(f"  world translate = {trans}   scale ≈ {scale}")

        for op_name in XFORM_OPS:
            a = prim.GetAttribute(op_name)
            if not a or not a.HasAuthoredValue():
                continue
            srcs = []
            for spec in a.GetPropertyStack(Usd.TimeCode.Default()):
                srcs.append(os.path.basename(spec.layer.identifier))
            print(f"  {op_name} = {a.Get()}   ← {' , '.join(srcs)}")

    print(f"\n同期対象の prim: {found} 件")
    if found == 0:
        print("→ ★ 同期対象が0件です。Blender からのエクスポート構造が想定と")
        print("     異なる可能性があります。")

    if args.broker:
        head("5. broker の応答との突き合わせ")
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = args.broker.rstrip("/") + "/scene?reload=1"
            with opener.open(url, timeout=30) as res:
                data = json.loads(res.read().decode("utf-8"))
            print(f"broker stage: metersPerUnit={data['stage']['metersPerUnit']} "
                  f"upAxis={data['stage']['upAxis']}")
            for o in data["objects"]:
                m = o["matrix_world"]
                t = [round(m[i][3], 4) for i in range(3)]
                s = [round(sum(m[r][c] ** 2 for r in range(3)) ** 0.5, 4)
                     for c in range(3)]
                print(f"  {o['blender_name']:<20} translate={t}  scale≈{s}")
            print("\n→ ここの値が Blender で期待する値と一致していれば、")
            print("   問題は Blender 側（オブジェクト名の不一致など）です。")
        except Exception as e:  # noqa: BLE001
            print(f"broker に接続できませんでした: {e}")

    head("まとめ")
    if not authored:
        print("最も可能性が高い原因: Isaac Sim の編集が root.usda に保存されていない")
        print("→ 上記 3 の a) b) c) を確認してください。")
    elif found == 0:
        print("最も可能性が高い原因: 同期対象の prim が見つからない")
    else:
        print("USD 側は正常です。Blender 側のログ「更新 N 件」を確認してください。")
        print("N が 0 なら、Blender のオブジェクト名と blender_name が不一致です。")


if __name__ == "__main__":
    main()
