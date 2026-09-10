"""
usd_pull_back.py -- Isaac Sim の編集を Blender に反映するスクリプト

Blender の Scripting タブに貼り付け、下の設定を書き換えて実行してください。

■ 前提
  別プロセスで broker.py が起動していること。
      .venv/bin/python broker.py --stage /path/to/root.usda
  このスクリプトは Blender 標準ライブラリ (urllib / json) しか使いません。
  usd-core を Blender 同梱 Python に入れる必要はありません
  （入れると tbb の同名衝突で Blender ごと落ちるため、入れないでください）。

■ 何をするか
  broker から受け取ったシーン情報をもとに、Blender の「既存オブジェクトを
  その場で更新」します。再インポートではないのでオブジェクトが重複せず、
  マテリアル・モディファイア・リグ・他のオブジェクトはすべて残ります。
"""

# ===== 設定 =====================================================
BROKER_URL = "http://127.0.0.1:8765"
AUTH_TOKEN = ""            # broker を --token 付きで起動した場合のみ設定

SYNC_TRANSFORMS  = True    # 位置・回転・スケールを反映
SYNC_VISIBILITY  = True    # 表示／非表示を反映
SYNC_MESH_POINTS = False   # 頂点数が一致する場合のみ頂点座標も反映

FORCE_RELOAD = True        # broker にファイルの再読み込みを促す
TIMEOUT_SEC  = 30.0
# ================================================================

import json
import urllib.error
import urllib.parse
import urllib.request

import bpy
import mathutils


# localhost 宛なので、環境変数のプロキシ設定は必ず無視する
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(route, **params):
    url = BROKER_URL.rstrip("/") + route
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if AUTH_TOKEN:
        req.add_header("X-Auth-Token", AUTH_TOKEN)
    try:
        with _opener.open(req, timeout=TIMEOUT_SEC) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"broker がエラーを返しました ({e.code}): {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"broker に接続できません ({BROKER_URL})。\n"
            f"  別のコンソールで broker.py が起動しているか確認してください:\n"
            f"    .venv/bin/python broker.py --stage /path/to/root.usda\n"
            f"  詳細: {e.reason}")


def main():
    health = fetch("/health")
    print(f"[pull_back] broker OK (USD {health.get('usd_version')}, "
          f"Python {health.get('python')})")

    params = {}
    if FORCE_RELOAD:
        params["reload"] = 1
    if SYNC_MESH_POINTS:
        params["points"] = 1
    scene = fetch("/scene", **params)

    if scene.get("schema") != 1:
        print(f"[pull_back] 警告: 未知の schema バージョン {scene.get('schema')}")

    meta = scene["stage"]
    print(f"[pull_back] stage: metersPerUnit={meta['metersPerUnit']} "
          f"upAxis={meta['upAxis']}")

    # broker はメートル・Z-up で返すので、あとは Blender のシーン単位に合わせるだけ
    scene_scale = bpy.context.scene.unit_settings.scale_length or 1.0
    conv = mathutils.Matrix.Scale(1.0 / scene_scale, 4)

    updated, unmatched, added, skipped = [], [], [], []

    for obj_info in scene["objects"]:
        name = obj_info["blender_name"]
        obj = bpy.data.objects.get(name)

        if obj is None:
            (unmatched if obj_info["from_blender"] else added).append(
                f"{obj_info['prim_path']} (-> {name})")
            continue

        if SYNC_TRANSFORMS:
            obj.matrix_world = conv @ mathutils.Matrix(obj_info["matrix_world"])

        if SYNC_VISIBILITY:
            hidden = not obj_info["visible"]
            obj.hide_viewport = hidden
            obj.hide_render = hidden

        if SYNC_MESH_POINTS and obj.type == "MESH" and "points" in obj_info:
            pts = obj_info["points"]
            if len(pts) == len(obj.data.vertices):
                for v, p in zip(obj.data.vertices, pts):
                    v.co = (p[0], p[1], p[2])
                obj.data.update()
            else:
                skipped.append(f"{name} (USD {len(pts)} / "
                               f"Blender {len(obj.data.vertices)})")

        updated.append(obj.name)

    print(f"[pull_back] 更新 {len(updated)} 件: {updated}")
    if unmatched:
        print(f"[pull_back] Blender 側に該当なし（名前変更？）: {unmatched}")
    if added:
        print(f"[pull_back] Isaac Sim 側で新規追加（手動で取込が必要）: {added}")
    if skipped:
        print(f"[pull_back] 頂点数不一致でスキップ: {skipped}")

    # ビューポートに即反映
    for area in getattr(bpy.context.screen, "areas", []):
        if area.type == "VIEW_3D":
            area.tag_redraw()


main()
