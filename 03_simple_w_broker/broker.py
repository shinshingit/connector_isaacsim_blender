#!/usr/bin/env python3
"""
broker.py -- Isaac Sim (USD) と Blender を中継する常駐サーバ

■ なぜ必要か
  usd-core を Blender 同梱 Python に入れると、usd-core が同梱する tbb と
  Blender が起動時にロード済みの tbb が同名で衝突し、import 時に落ちます
  （特に Windows）。そこで USD を読む処理を Blender プロセスから切り離し、
  独立した仮想環境で常駐サーバとして動かします。
  Blender 側は標準ライブラリ（urllib / json）だけで済むため、
  Blender に一切の追加依存を入れずに済みます。

■ 構成
    Isaac Sim  --(root.usda を保存)-->  [ファイル]
                                           |
                                           v  usd-core で読む
    Blender  --(HTTP / JSON)-->  broker.py (venv, 常駐)

■ 起動方法
    python -m venv .venv
    .venv/bin/pip install usd-core            # Windows: .venv\\Scripts\\pip
    .venv/bin/python broker.py --stage /path/to/root.usda

    起動したままにしておき、Blender 側から叩きます。Ctrl+C で停止。

■ エンドポイント
    GET /health   サーバ稼働確認とバージョン情報
    GET /stage    stage のメタデータのみ（metersPerUnit / upAxis / mtime）
    GET /scene    シーン全体のスナップショット（Blender 側が使う本体）
                  ?reload=1  ファイルの再読み込みを強制
                  ?points=1  メッシュ頂点座標も含める
"""

import argparse
import json
import os
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from pxr import Usd, UsdGeom
except ImportError:
    sys.exit(
        "pxr が見つかりません。usd-core を入れた仮想環境の python で実行してください:\n"
        "    python -m venv .venv\n"
        "    .venv/bin/pip install usd-core\n"
        "    .venv/bin/python broker.py --stage /path/to/root.usda"
    )

SCHEMA_VERSION = 1
BLENDER_NAME_ATTR = "userProperties:blender:object_name"

_lock = threading.Lock()
_state = {"stage": None, "path": None, "layer_mtimes": {}}


# --------------------------------------------------------------------------
# 行列ユーティリティ
#   USD の Gf.Matrix4d は行ベクトル規約 (v * M)、Blender の mathutils.Matrix は
#   列ベクトル規約 (M * v)。転置して Blender 側でそのまま Matrix() に渡せる
#   「行優先・平行移動が最終列」の形にして返す。
# --------------------------------------------------------------------------
def identity():
    return [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]


def mat_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def usd_to_rows(m):
    """Gf.Matrix4d -> Blender 規約の 4x4 リスト（転置）。"""
    return [[float(m[c][r]) for c in range(4)] for r in range(4)]


def conversion_matrix(meters_per_unit, up_axis):
    """USD stage 空間 -> メートル・Z-up 空間 への変換行列。

    Blender のシーン単位 (scale_length) の反映は Blender 側で行う。
    """
    conv = identity()
    for i in range(3):
        conv[i][i] = float(meters_per_unit)
    if str(up_axis).upper().startswith("Y"):
        rot_x90 = [[1.0, 0.0, 0.0, 0.0],
                   [0.0, 0.0, -1.0, 0.0],
                   [0.0, 1.0, 0.0, 0.0],
                   [0.0, 0.0, 0.0, 1.0]]
        conv = mat_mul(rot_x90, conv)
    return conv


# --------------------------------------------------------------------------
# stage の読み込みとキャッシュ
# --------------------------------------------------------------------------
def layer_mtimes(stage):
    out = {}
    for layer in stage.GetUsedLayers():
        p = layer.realPath
        if p and os.path.exists(p):
            out[p] = os.path.getmtime(p)
    return out


def get_stage(force_reload=False):
    """必要なら再読込して stage を返す。いずれかのレイヤが更新されていたら reload。

    注意: USD はレイヤをプロセス内でキャッシュするため、Usd.Stage.Open() を
    呼び直してもディスク上の変更は反映されない。必ず stage.Reload() を使う。
    """
    with _lock:
        path = _state["path"]
        stage = _state["stage"]

        if not os.path.exists(path):
            raise FileNotFoundError(path)

        if stage is not None:
            if force_reload or layer_mtimes(stage) != _state["layer_mtimes"]:
                stage.Reload()
                _state["layer_mtimes"] = layer_mtimes(stage)
                print(f"[broker] stage を再読み込みしました: {path}", flush=True)
            return stage

        stage = Usd.Stage.Open(path)
        if stage is None:
            raise RuntimeError(f"USD を開けませんでした: {path}")
        _state["stage"] = stage
        _state["layer_mtimes"] = layer_mtimes(stage)
        print(f"[broker] stage を読み込みました: {path}", flush=True)
        return stage


def stage_metadata(stage, args):
    mpu = args.meters_per_unit
    if mpu is None:
        mpu = UsdGeom.GetStageMetersPerUnit(stage)
    up = args.up_axis
    if up is None:
        up = str(UsdGeom.GetStageUpAxis(stage))
    mtimes = dict(_state["layer_mtimes"])
    newest = max(mtimes.values()) if mtimes else 0.0
    # root.usda 単体の更新時刻。「Isaac Sim が保存したか」の判定に使う
    # （Blender の自動エクスポートで base.usdc が変わっても、これは動かない）
    root_mtime = mtimes.get(_state["path"], 0.0)
    return {
        "path": _state["path"],
        "metersPerUnit": float(mpu),
        "upAxis": str(up).upper()[:1],
        "layers": sorted(mtimes.keys()),
        "layer_mtimes": mtimes,
        "root_mtime": root_mtime,
        "mtime": newest,
    }


def is_blender_generated_root(prim):
    """Blender のエクスポータが自動生成した /root を除外する。"""
    cd = prim.GetCustomData() or {}
    return bool(cd.get("Blender", {}).get("generated", False))


def transform_conflicts(stage):
    """役割分担ルール違反の検出。

    Isaac Sim が transform を編集すると xformOpOrder が丸ごと置き換わり、
    base.usdc（Blender）側の translate / rotate が「存在するのに適用されない」
    状態になる。これは黙って起きるので、ここで拾って警告する。
    """
    out = []
    root_layer = stage.GetRootLayer()

    def meaningful(op_name, value):
        if value is None:
            return False
        if "scale" in op_name:
            return any(abs(v - 1.0) > 1e-6 for v in value)
        if "translate" in op_name or "rotate" in op_name:
            return any(abs(v) > 1e-6 for v in value)
        return True

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Xformable) or is_blender_generated_root(prim):
            continue
        attr = prim.GetAttribute("xformOpOrder")
        if not attr or not attr.HasAuthoredValue():
            continue
        stack = attr.GetPropertyStack(Usd.TimeCode.Default())
        if len(stack) < 2 or stack[0].layer != root_layer:
            continue

        composed = list(attr.Get() or [])
        dropped = []
        for spec in stack[1:]:
            for op in (spec.default or []):
                if op in composed or op in [d["op"] for d in dropped]:
                    continue
                op_spec = spec.layer.GetAttributeAtPath(
                    prim.GetPath().AppendProperty(op))
                value = op_spec.default if op_spec else None
                if meaningful(op, value):
                    dropped.append({
                        "op": str(op),
                        "value": [float(v) for v in value],
                        "layer": os.path.basename(spec.layer.identifier),
                    })
        if dropped:
            out.append({
                "prim_path": str(prim.GetPath()),
                "dropped": dropped,
                "composed_order": [str(o) for o in composed],
            })
    return out


def build_scene(stage, args, include_points):
    meta = stage_metadata(stage, args)
    conv = conversion_matrix(meta["metersPerUnit"], meta["upAxis"])
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    objects = []

    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Xformable) or is_blender_generated_root(prim):
            continue
        # Mesh 本体ではなく、その親の Xform が Blender の「オブジェクト」に対応する
        if prim.IsA(UsdGeom.Mesh) and prim.GetParent().IsA(UsdGeom.Xformable):
            continue

        attr = prim.GetAttribute(BLENDER_NAME_ATTR)
        has_name = bool(attr and attr.HasAuthoredValue())
        blender_name = attr.Get() if has_name else prim.GetName()

        world = mat_mul(conv, usd_to_rows(
            cache.GetLocalToWorldTransform(prim)))
        visible = (UsdGeom.Imageable(prim)
                   .ComputeVisibility(Usd.TimeCode.Default())
                   != UsdGeom.Tokens.invisible)

        entry = {
            "prim_path": str(prim.GetPath()),
            "prim_name": prim.GetName(),
            "blender_name": blender_name,
            "from_blender": has_name,   # False = Isaac Sim 側で新規追加された prim
            "matrix_world": world,      # メートル・Z-up、Blender 規約の行優先 4x4
            "visible": visible,
        }

        if include_points:
            mesh_prim = next((c for c in prim.GetChildren()
                              if c.IsA(UsdGeom.Mesh)), None)
            if mesh_prim is not None:
                pts = UsdGeom.Mesh(mesh_prim).GetPointsAttr().Get()
                if pts:
                    s = meta["metersPerUnit"]
                    entry["points"] = [[p[0] * s, p[1] * s, p[2] * s]
                                       for p in pts]
                    entry["point_count"] = len(pts)

        objects.append(entry)

    conflicts = transform_conflicts(stage)
    if conflicts != _state.get("last_conflicts"):
        _state["last_conflicts"] = conflicts
        for c in conflicts:
            ops = ", ".join(f"{d['op']}={d['value']}" for d in c["dropped"])
            print(f"[broker] ★ 役割分担ルール違反: {c['prim_path']}", flush=True)
            print(f"          Blender 側の {ops} が Isaac Sim の transform 編集に"
                  f"打ち消されています", flush=True)

    return {"schema": SCHEMA_VERSION, "stage": meta,
            "objects": objects, "conflicts": conflicts}


# --------------------------------------------------------------------------
# HTTP サーバ
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    args = None
    server_version = "usd-broker/1.0"

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        # ブラウザ経由のアクセスは想定しないので Origin 付きは弾く
        if self.headers.get("Origin"):
            return False
        token = self.args.token
        if not token:
            return True
        return self.headers.get("X-Auth-Token") == token

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        route = parsed.path.rstrip("/") or "/"

        if not self._authorized():
            self._send(403, {"error": "forbidden"})
            return

        try:
            if route == "/health":
                self._send(200, {
                    "status": "ok",
                    "schema": SCHEMA_VERSION,
                    "usd_version": ".".join(str(v) for v in Usd.GetVersion()),
                    "python": sys.version.split()[0],
                    "stage_path": self.args.stage,
                })
                return

            force = query.get("reload", ["0"])[0] not in ("0", "", "false")
            stage = get_stage(force_reload=force)

            if route == "/stage":
                self._send(200, stage_metadata(stage, self.args))
            elif route == "/scene":
                want_points = query.get("points", ["0"])[0] not in ("0", "", "false")
                self._send(200, build_scene(stage, self.args, want_points))
            else:
                self._send(404, {"error": "not found",
                                 "routes": ["/health", "/stage", "/scene"]})
        except FileNotFoundError as e:
            self._send(404, {"error": f"USD ファイルが見つかりません: {e}"})
        except Exception as e:  # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *a):
        if self.args.verbose:
            sys.stderr.write("[broker] %s - %s\n" % (self.address_string(), fmt % a))


def main():
    ap = argparse.ArgumentParser(description="Isaac Sim <-> Blender USD broker")
    ap.add_argument("--stage", required=True,
                    help="Isaac Sim が保存したルート USD (root.usda)")
    ap.add_argument("--host", default="127.0.0.1",
                    help="待ち受けアドレス (既定: 127.0.0.1、localhost のみ)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--token", default="",
                    help="任意の共有トークン。指定すると X-Auth-Token ヘッダを要求する")
    ap.add_argument("--meters-per-unit", type=float, default=None,
                    help="stage メタデータを信用せず単位を強制する (例: 1.0)")
    ap.add_argument("--up-axis", default=None, choices=["Z", "Y", "z", "y"],
                    help="stage メタデータを信用せず上方向を強制する")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    args.stage = os.path.abspath(args.stage)
    _state["path"] = args.stage
    Handler.args = args

    # 起動時に一度読んで、設定ミスをすぐ気づけるようにする
    try:
        stage = get_stage(force_reload=True)
        meta = stage_metadata(stage, args)
        print(f"[broker] metersPerUnit={meta['metersPerUnit']} "
              f"upAxis={meta['upAxis']}", flush=True)
        if meta["metersPerUnit"] != 1.0 or meta["upAxis"] != "Z":
            print("[broker] 警告: Blender 想定 (metersPerUnit=1, upAxis=Z) と異なります。"
                  "root.usda のルートレイヤにメタデータが書かれているか確認してください。",
                  flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[broker] 起動時の読み込みに失敗: {e}", flush=True)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[broker] http://{args.host}:{args.port} で待機中 "
          f"(stage: {args.stage})", flush=True)
    print("[broker] 停止するには Ctrl+C", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[broker] 停止しました", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
