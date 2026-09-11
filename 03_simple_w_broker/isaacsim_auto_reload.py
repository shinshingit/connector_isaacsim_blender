"""
isaacsim_auto_reload.py -- Blender の保存を検知して base.usdc を自動リロードする

Isaac Sim の Window > Script Editor に貼り付け、BASE_USD を書き換えて実行します。
一度実行すると常駐し、Blender 側で上書き保存するたびに base.usdc レイヤを
自動で読み直します（root.usda 側の編集はそのまま残ります）。

■ 前提
  isaacsim_setup_layer.py で作った root.usda を開いていること。
      root.usda
       └ subLayer: base.usdc   ← このレイヤだけをリロードする

■ 停止のしかた
  下の MODE を "stop" にして、もう一度実行してください。
  Isaac Sim を再起動しても止まります。

■ 注意
  リロードするのは base.usdc だけです。root.usda（Isaac Sim 自身の編集）は
  触らないので、未保存の編集が消えることはありません。
"""

# ===== 設定 =====================================================
MODE = "auto"      # "auto" = 常駐開始 / "stop" = 常駐解除

BASE_USD = r"C:/usd_bridge/base.usdc"   # Blender が書き出すファイル
POLL_INTERVAL_SEC = 2.0
VERBOSE = True
# ================================================================

import builtins
import os
import time

import omni.kit.app
import omni.usd
from pxr import Sdf

# Script Editor を再実行しても二重登録にならないよう、状態をここに保持する
STATE = getattr(builtins, "_usd_sync_isaac", None)
if STATE is None:
    STATE = {}
    builtins._usd_sync_isaac = STATE


def log(*a):
    if VERBOSE:
        print("[usd_sync/isaac]", *a)


def find_layer(target_path):
    """開いている stage の中から base.usdc に対応するレイヤを探す。"""
    stage = omni.usd.get_context().get_stage()
    if stage is None:
        return None
    target = os.path.normcase(os.path.abspath(target_path))
    for layer in stage.GetUsedLayers():
        real = layer.realPath
        if real and os.path.normcase(os.path.abspath(real)) == target:
            return layer
    # フォールバック: 識別子で直接探す
    return Sdf.Layer.Find(target_path)


def _on_update(_event):
    now = time.time()
    if now - STATE.get("last_check", 0.0) < STATE.get("interval", 2.0):
        return
    STATE["last_check"] = now

    path = STATE.get("base_usd")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return

    if mtime == STATE.get("mtime"):
        STATE["pending"] = None
        return

    # Blender が書き込み中のファイルを掴まないよう、
    # 「1周期のあいだ mtime が変わらない」ことを確認してからリロードする
    if STATE.get("pending") != mtime:
        STATE["pending"] = mtime
        return

    STATE["mtime"] = mtime
    STATE["pending"] = None

    layer = find_layer(path)
    if layer is None:
        log(f"★ base.usdc が現在の stage に含まれていません: {path}")
        return
    try:
        layer.Reload(force=True)
        log(f"Blender の保存を検知 -> base.usdc をリロードしました")
    except Exception as e:  # noqa: BLE001
        log(f"★ リロードに失敗（次の周期で再試行します）: {type(e).__name__}: {e}")
        STATE["mtime"] = None


def stop():
    sub = STATE.pop("sub", None)
    if sub is not None:
        try:
            sub.unsubscribe()
        except Exception:  # noqa: BLE001
            pass
    STATE["active"] = False
    log("常駐を解除しました")


def start():
    stop()   # 二重登録を防ぐ

    if not os.path.exists(BASE_USD):
        log(f"★ base.usdc が見つかりません: {BASE_USD}")
        log("  パスを確認してください（Blender 側の BASE_USD と同じ値にします）")
        return

    layer = find_layer(BASE_USD)
    if layer is None:
        log(f"★ 警告: base.usdc が現在の stage の subLayer に含まれていません。")
        log(f"  isaacsim_setup_layer.py で作った root.usda を開いていますか？")

    STATE.update({
        "active": True,
        "base_usd": os.path.abspath(BASE_USD),
        "interval": POLL_INTERVAL_SEC,
        "mtime": os.path.getmtime(BASE_USD),
        "pending": None,
        "last_check": 0.0,
    })

    STATE["sub"] = (omni.kit.app.get_app()
                    .get_update_event_stream()
                    .create_subscription_to_pop(_on_update,
                                                name="usd_sync_base_watch"))
    log(f"Blender の保存を {POLL_INTERVAL_SEC} 秒間隔で監視します: {BASE_USD}")
    log('停止するには MODE = "stop" で再実行してください。')


if MODE == "stop":
    stop()
elif MODE == "auto":
    start()
else:
    raise ValueError(f'MODE は "auto" / "stop" のいずれかです: {MODE!r}')
