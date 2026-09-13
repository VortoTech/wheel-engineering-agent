"""Local window-annotation server for wheel photos.

The photo folder is only read. Labels are keyed by image SHA-256 (renames and duplicate copies share
one label) and written to --out, by default data/annotations/ in the repo, which is not tracked by git.
Coordinates are pixels of the EXIF-oriented original image.

Usage: .venv/bin/python experiments/annotate/serve.py PHOTO_DIR [--out DIR] [--port 8770]
"""
import argparse
import hashlib
import io
import json
import math
import os
import re
import tempfile
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PREVIEW_MAX = 2000
FORMAT = "wheel-window-labels/v1"
META_KEYS = {"structure": str, "view": str, "background": str, "style": str, "groups": int, "usable": bool, "note": str}


def oriented_size(image):
    # EXIF orientations 5–8 swap width and height; reading the tag avoids decoding large photos.
    w, h = image.size
    return (h, w) if image.getexif().get(0x0112) in (5, 6, 7, 8) else (w, h)


def scan(folder: Path):
    items, first = [], {}
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with Image.open(path) as image:
            width, height = oriented_size(image)
        items.append({"index": len(items), "name": path.name, "sha256": digest, "width": width, "height": height,
                      "duplicate_of": first.get(digest), "path": path})
        first.setdefault(digest, len(items) - 1)
    return items


def _points(value, width, height, minimum):
    if not isinstance(value, list) or len(value) < minimum or len(value) > 2000:
        raise ValueError(f"至少需要 {minimum} 个点")
    points = []
    for point in value:
        if (not isinstance(point, list) or len(point) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)):
            raise ValueError("点坐标格式错误")
        x, y = point
        if not (-0.02 * width <= x <= 1.02 * width and -0.02 * height <= y <= 1.02 * height):
            raise ValueError("点超出图片范围")
        points.append([round(float(x), 2), round(float(y), 2)])
    return points


def _ellipse(value, width, height):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("椭圆格式错误")
    result = {"points": _points(value.get("points"), width, height, 5)}
    for key in ("cx", "cy", "a", "b", "angle_deg", "rms_px"):
        number = value.get(key)
        if not isinstance(number, (int, float)) or not math.isfinite(number):
            raise ValueError(f"椭圆字段 {key} 无效")
        result[key] = float(number)
    if result["a"] <= 0 or result["b"] <= 0:
        raise ValueError("椭圆半轴必须为正")
    return result


def validate(body, item):
    if not isinstance(body, dict):
        raise ValueError("标注必须是对象")
    w, h = item["width"], item["height"]
    meta = body.get("meta") or {}
    if not isinstance(meta, dict) or set(meta) - set(META_KEYS):
        raise ValueError("元数据字段无效")
    clean_meta = {}
    for key, value in meta.items():
        kind = META_KEYS[key]
        if value in ("", None):
            continue
        if kind is int and not (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 60):
            raise ValueError("辐条组数须为 0–60 的整数")
        if kind is bool and not isinstance(value, bool):
            raise ValueError("可用于评价须为布尔值")
        if kind is str and (not isinstance(value, str) or len(value) > 500):
            raise ValueError(f"{key} 须为不超过 500 字的文本")
        clean_meta[key] = value
    polygons = {}
    for key in ("windows", "ignore"):
        value = body.get(key) or []
        if not isinstance(value, list) or len(value) > 400:
            raise ValueError(f"{key} 格式错误")
        polygons[key] = [{"points": _points(p.get("points") if isinstance(p, dict) else None, w, h, 3)} for p in value]
    return {"format": FORMAT,
            "image": {"name": item["name"], "sha256": item["sha256"], "width": w, "height": h},
            "rim": _ellipse(body.get("rim"), w, h), "hub": _ellipse(body.get("hub"), w, h),
            "windows": polygons["windows"], "ignore": polygons["ignore"], "meta": clean_meta,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def make_handler(items, out: Path):
    by_sha = {item["sha256"]: item for item in items}
    previews, lock = {}, threading.Lock()

    def preview(index):
        with lock:
            if index not in previews:
                with Image.open(items[index]["path"]) as image:
                    image = ImageOps.exif_transpose(image).convert("RGB")
                    image.thumbnail((PREVIEW_MAX, PREVIEW_MAX))
                    buffer = io.BytesIO()
                    image.save(buffer, "JPEG", quality=88)
                previews[index] = buffer.getvalue()
            return previews[index]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, kind="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                return self.send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/images":
                return self.send(200, {"out": str(out), "items": [
                    {**{k: v for k, v in item.items() if k != "path"},
                     "labeled": (out / f"{item['sha256']}.json").exists()} for item in items]})
            match = re.fullmatch(r"/api/images/(\d+)/preview", self.path)
            if match and int(match[1]) < len(items):
                return self.send(200, preview(int(match[1])), "image/jpeg")
            match = re.fullmatch(r"/api/labels/([0-9a-f]{64})", self.path)
            if match and match[1] in by_sha:
                path = out / f"{match[1]}.json"
                return self.send(200, path.read_bytes()) if path.exists() else self.send(404, {"detail": "尚未标注"})
            self.send(404, {"detail": "不存在"})

        def do_PUT(self):
            match = re.fullmatch(r"/api/labels/([0-9a-f]{64})", self.path)
            if not match or match[1] not in by_sha:
                return self.send(404, {"detail": "图片不存在"})
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= 5_000_000:
                return self.send(413, {"detail": "标注过大或为空"})
            try:
                record = validate(json.loads(self.rfile.read(length)), by_sha[match[1]])
            except (ValueError, json.JSONDecodeError) as exc:
                return self.send(422, {"detail": str(exc)})
            out.mkdir(parents=True, exist_ok=True)
            # Atomic replace: an interrupted save never leaves a truncated label behind.
            with tempfile.NamedTemporaryFile("w", dir=out, delete=False, suffix=".tmp", encoding="utf-8") as tmp:
                json.dump(record, tmp, ensure_ascii=False, indent=1)
            os.replace(tmp.name, out / f"{match[1]}.json")
            self.send(200, record)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "annotations")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    items = scan(args.folder)
    if not items:
        raise SystemExit(f"{args.folder} 中没有 jpg / png / webp 图片")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(items, args.out.resolve()))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"{len(items)} 张图片，标注保存到 {args.out.resolve()}\n打开 {url} ，Ctrl+C 退出")
    if not args.no_browser:
        webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
