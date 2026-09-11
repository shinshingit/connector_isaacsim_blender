"""
usd_pull_back.py -- Blender ⇄ Isaac Sim 自動同期スクリプト

Blender の Scripting タブに貼り付け、下の設定を書き換えて実行してください。
一度実行すると常駐し、以降は「上書き保存」がトリガーになって双方向に同期します。

    Blender で Ctrl+S   →  base.usdc を自動エクスポート
    Isaac Sim で Ctrl+S →  Blender 側のオブジェクトを自動更新（ポーリング検知）

■ 前提
  別プロセスで broker.py が起動していること。
      .venv/bin/python broker.py --stage C:/usd_bridge/root.usda
  このスクリプトは Blender 標準ライブラリ (urllib / json) しか使いません。
  usd-core を Blender 同梱 Python に入れる必要はありません
  （入れると tbb の同名衝突で Blender ごと落ちるため、入れないでください）。

■ 停止のしかた
  下の MODE を "stop" にして、もう一度実行してください。
  Blender を再起動しても止まります（常駐は .blend には保存されません）。
"""

# ===== 設定 =====================================================
MODE = "auto"
# "auto" : 常駐して自動同期する（通常はこれ）
# "once" : いまの Isaac Sim の状態を1回だけ反映して終了する
# "stop" : 常駐を解除する

BROKER_URL = "http://127.0.0.1:8765"
AUTH_TOKEN = ""            # broker を --token 付きで起動した場合のみ設定

# --- Blender 上書き保存 → base.usdc の自動エクスポート ---
AUTO_EXPORT_ON_SAVE = True
BASE_USD = r"C:/usd_bridge/base.usdc"   # broker の root.usda が参照している base
EXPORT_OPTIONS = {
    "selected_objects_only": False,
    "export_animation": False,
    "export_materials": True,
}

# --- Isaac Sim 保存 → Blender への自動反映 ---
AUTO_PULL = True
POLL_INTERVAL_SEC = 2.0    # root.usda の更新をこの間隔で確認する

SYNC_TRANSFORMS  = True    # 位置・回転・スケールを反映
SYNC_VISIBILITY  = True    # 表示／非表示を反映
SYNC_MESH_POINTS = False   # 頂点数が一致する場合のみ頂点座標も反映

POLL_TIMEOUT_SEC = 3.0     # ポーリング用（短くしないと UI が固まる）
FETCH_TIMEOUT_SEC = 30.0   # 本体取得用
VERBOSE = True
# ================================================================

import json
import os
import sys
import types
import urllib.error
import urllib.parse
import urllib.request

import bpy
import mathutils
from bpy.app.handlers import persistent

# 再実行しても二重登録にならないよう、状態をここに保持する。
# bpy.app.driver_namespace は .blend を読み込むたびにクリアされてしまい、
# @persistent ハンドラだけが生き残って「STATE が空なので何もしない」状態に
# なるため、Blender プロセスと寿命を共にする sys.modules に置く。
_STORE_KEY = "_usd_sync_store"
if _STORE_KEY not in sys.modules:
    _mod = types.ModuleType(_STORE_KEY)
    _mod.state = {}
    sys.modules[_STORE_KEY] = _mod
STATE = sys.modules[_STORE_KEY].state

# localhost 宛なので、環境変数のプロキシ設定は必ず無視する
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def log(*a):
    if VERBOSE:
        print("[usd_sync]", *a)


# --------------------------------------------------------------------------
# broker との通信
# --------------------------------------------------------------------------
def fetch(route, timeout, **params):
    url = BROKER_URL.rstrip("/") + route
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if AUTH_TOKEN:
        req.add_header("X-Auth-Token", AUTH_TOKEN)
    try:
        with _opener.open(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"broker がエラーを返しました ({e.code}): {detail}")
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"broker に接続できません ({BROKER_URL})。\n"
            f"  別のコンソールで broker.py が起動しているか確認してください:\n"
            f"    .venv/bin/python broker.py --stage C:/usd_bridge/root.usda\n"
            f"  詳細: {e.reason}")


# --------------------------------------------------------------------------
# Isaac Sim → Blender（プル）
# --------------------------------------------------------------------------
def apply_scene(scene):
    if scene.get("schema") != 1:
        log(f"警告: 未知の schema バージョン {scene.get('schema')}")

    meta = scene["stage"]
    scene_scale = bpy.context.scene.unit_settings.scale_length or 1.0
    conv = mathutils.Matrix.Scale(1.0 / scene_scale, 4)

    updated, unmatched, added, skipped = [], [], [], []

    for info in scene["objects"]:
        name = info["blender_name"]
        obj = bpy.data.objects.get(name)
        if obj is None:
            (unmatched if info["from_blender"] else added).append(
                f"{info['prim_path']} (-> {name})")
            continue

        if SYNC_TRANSFORMS:
            obj.matrix_world = conv @ mathutils.Matrix(info["matrix_world"])

        if SYNC_VISIBILITY:
            hidden = not info["visible"]
            obj.hide_viewport = hidden
            obj.hide_render = hidden

        if SYNC_MESH_POINTS and obj.type == "MESH" and "points" in info:
            pts = info["points"]
            if len(pts) == len(obj.data.vertices):
                for v, p in zip(obj.data.vertices, pts):
                    v.co = (p[0], p[1], p[2])
                obj.data.update()
            else:
                skipped.append(f"{name} (USD {len(pts)} / "
                               f"Blender {len(obj.data.vertices)})")

        updated.append(obj.name)

    log(f"stage: metersPerUnit={meta['metersPerUnit']} upAxis={meta['upAxis']}")
    log(f"更新 {len(updated)} 件: {updated}")

    for c in scene.get("conflicts", []):
        ops = ", ".join(f"{d['op']}={d['value']}" for d in c["dropped"])
        print(f"[usd_sync] ★ 役割分担ルール違反: {c['prim_path']}")
        print(f"           Blender 側の {ops} が Isaac Sim の transform 編集に"
              f"打ち消されています。")
        print(f"           → そのオブジェクトの配置は Isaac Sim 側で行ってください。")
    if unmatched:
        log(f"Blender 側に該当なし（名前変更？）: {unmatched}")
    if added:
        log(f"Isaac Sim 側で新規追加（手動で取込が必要）: {added}")
    if skipped:
        log(f"頂点数不一致でスキップ: {skipped}")

    for area in getattr(bpy.context.screen, "areas", []):
        if area.type == "VIEW_3D":
            area.tag_redraw()

    return len(updated)


def pull_now(force_reload=True):
    params = {}
    if force_reload:
        params["reload"] = 1
    if SYNC_MESH_POINTS:
        params["points"] = 1
    scene = fetch("/scene", FETCH_TIMEOUT_SEC, **params)
    apply_scene(scene)
    STATE["root_mtime"] = scene["stage"].get("root_mtime", 0.0)


# --------------------------------------------------------------------------
# Blender 上書き保存 → base.usdc の自動エクスポート
# --------------------------------------------------------------------------
@persistent
def _on_save_post(_dummy=None, _dummy2=None):
    if not STATE.get("active") or not STATE.get("auto_export"):
        return
    path = STATE.get("base_usd")
    try:
        folder = os.path.dirname(path)
        if folder and not os.path.isdir(folder):
            os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.usd_export(filepath=path, **STATE.get("export_options", {}))
        log(f"base.usdc をエクスポートしました -> {path}")
    except Exception as e:  # noqa: BLE001
        # 保存そのものを失敗させたくないので握りつぶして報告だけする
        print(f"[usd_sync] ★ base.usdc のエクスポートに失敗: "
              f"{type(e).__name__}: {e}")
        return

    # 自分のエクスポートで base.usdc が変わっただけなので、
    # これを「Isaac Sim が保存した」と誤検知しないよう基準を取り直す
    try:
        meta = fetch("/stage", POLL_TIMEOUT_SEC)
        STATE["root_mtime"] = meta.get("root_mtime", STATE.get("root_mtime", 0.0))
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Isaac Sim の保存を検知するポーリング
# --------------------------------------------------------------------------
def _poll():
    if not STATE.get("active") or not STATE.get("auto_pull"):
        return None   # タイマー解除

    interval = STATE.get("interval", 2.0)

    # 編集モード中に差し替えると壊れるので待つ
    if bpy.context.mode != "OBJECT":
        return interval

    try:
        # reload は付けない。broker はレイヤの更新時刻を見て自動で読み直すので、
        # ここで強制すると 2 秒ごとに stage 全体の再読込が走ってしまう。
        meta = fetch("/stage", POLL_TIMEOUT_SEC)
    except Exception as e:  # noqa: BLE001
        # broker が落ちている間は静かに待つ（毎回ログを出さない）
        if not STATE.get("warned_down"):
            print(f"[usd_sync] broker に接続できません。復帰を待ちます: {e}")
            STATE["warned_down"] = True
        return max(interval, 5.0)

    if STATE.pop("warned_down", None):
        log("broker に再接続しました")

    root_mtime = meta.get("root_mtime", 0.0)
    if root_mtime and root_mtime != STATE.get("root_mtime"):
        log("Isaac Sim 側の保存を検知しました")
        try:
            pull_now(force_reload=False)
        except Exception as e:  # noqa: BLE001
            print(f"[usd_sync] ★ 反映に失敗: {type(e).__name__}: {e}")
            STATE["root_mtime"] = root_mtime

    return interval


# --------------------------------------------------------------------------
# 登録 / 解除
# --------------------------------------------------------------------------
def stop():
    STATE["active"] = False
    for h in list(bpy.app.handlers.save_post):
        if getattr(h, "__name__", "") == "_on_save_post":
            bpy.app.handlers.save_post.remove(h)
    old_timer = STATE.get("timer")
    if old_timer is not None and bpy.app.timers.is_registered(old_timer):
        bpy.app.timers.unregister(old_timer)
    STATE["timer"] = None
    log("常駐を解除しました")


def start():
    stop()   # 二重登録を防ぐ
    STATE.update({
        "active": True,
        "auto_export": AUTO_EXPORT_ON_SAVE,
        "auto_pull": AUTO_PULL,
        "base_usd": BASE_USD,
        "export_options": dict(EXPORT_OPTIONS),
        "interval": POLL_INTERVAL_SEC,
        "root_mtime": None,
    })

    health = fetch("/health", FETCH_TIMEOUT_SEC)
    log(f"broker OK (USD {health.get('usd_version')}, "
        f"Python {health.get('python')})")

    if AUTO_EXPORT_ON_SAVE:
        bpy.app.handlers.save_post.append(_on_save_post)
        log(f"上書き保存時に自動エクスポート: {BASE_USD}")

    if AUTO_PULL:
        STATE["timer"] = _poll
        bpy.app.timers.register(_poll, first_interval=1.0, persistent=True)
        log(f"Isaac Sim の保存を {POLL_INTERVAL_SEC} 秒間隔で監視します")

    # 起動直後に現状を一度取り込む
    pull_now()
    log("常駐を開始しました。停止するには MODE = \"stop\" で再実行してください。")


if MODE == "stop":
    stop()
elif MODE == "once":
    pull_now()
elif MODE == "auto":
    start()
else:
    raise ValueError(f'MODE は "auto" / "once" / "stop" のいずれかです: {MODE!r}')
