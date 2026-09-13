"""Ask Qwen / Wan image models (Alibaba Cloud Model Studio) for orthographic views of a wheel photo.

Default route is the user's relay (/v1/images/edits, RELAY_BASE_URL + RELAY_API_KEY); --via dashscope
calls DashScope directly with DASHSCOPE_API_KEY. Keys come from the environment or the repo's .env
(gitignored) and are never printed. The photo is uploaded to Alibaba Cloud; only run this on photos
you are allowed to share.

Usage: generate.py PHOTO [--out DIR] [--model qwen-image-3.0] [--via relay] [--views front_silhouette front_render section]
"""
import argparse
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
PROMPTS = {
    # Silhouette: easiest to measure — solid face in black, see-through openings white.
    "front_silhouette": "把这只轮毂画成正交正视图（沿轮毂轴线正对，无透视、无倾斜）。纯白背景。轮毂正面所有实体"
                        "（轮辐、中心盘、轮唇）用纯黑色填充，所有能透过去的开口用纯白色。不要阴影、高光、文字和纹理。"
                        "保持原图的辐条数量、分组方式和每根辐条的形状与粗细不变。",
    "front_render": "把这只轮毂重新渲染成正交正视图：沿轮毂轴线正对，无透视、无倾斜，纯白背景，柔和均匀光照。"
                    "保持原图的辐条数量、分组方式、形状、粗细和颜色不变，不要添加或删除任何结构。",
    "section": "画出这只轮毂的工程剖面图：沿一根轮辐中线、通过轮毂轴线切开，只画上半部分截面。纯白背景，黑色线条，"
               "截面区域用斜线填充。要表达轮辋截面、轮缘、轮辐厚度与凹面深度、中心盘和安装面。不要透视。",
}


def setting(name):
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.strip().startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def jpeg(photo: Path, max_side=1536):
    with Image.open(photo) as image:
        image = ImageOps.exif_transpose(image).convert("RGB")
        image.thumbnail((max_side, max_side))
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def relay_request(base, key, model, image_bytes, prompt):
    """OpenAI-style multipart /images/edits on the relay; it forwards to DashScope synchronously."""
    boundary = "----wheelcam" + os.urandom(8).hex()
    parts = []
    for name, value in (("model", model), ("prompt", prompt), ("watermark", "false")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="photo.jpg"\r\n'
                 f"Content-Type: image/jpeg\r\n\r\n".encode() + image_bytes + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(base.rstrip("/") + "/images/edits", data=b"".join(parts), method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:500]}") from None
    item = result["data"][0]
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"]), result.get("id")
    with urllib.request.urlopen(item["url"], timeout=120) as response:
        return response.read(), result.get("id")


def request(key, model, image, prompt):
    body = {"model": model,
            "input": {"messages": [{"role": "user", "content": [{"image": image}, {"text": prompt}]}]},
            "parameters": {"watermark": False}}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("photo", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts" / "genviews")
    parser.add_argument("--model", default="qwen-image-3.0")
    parser.add_argument("--views", nargs="+", default=list(PROMPTS), choices=list(PROMPTS))
    parser.add_argument("--via", choices=["relay", "dashscope"], default="relay",
                        help="relay: RELAY_BASE_URL + RELAY_API_KEY；dashscope: DASHSCOPE_API_KEY")
    args = parser.parse_args()
    names = ("RELAY_BASE_URL", "RELAY_API_KEY") if args.via == "relay" else ("DASHSCOPE_API_KEY",)
    values = [setting(n) for n in names]
    if not all(values):
        raise SystemExit(f"缺少 {' / '.join(names)}：请写入仓库根目录 .env（已被 git 忽略）或设置环境变量。")
    image_bytes = jpeg(args.photo)
    args.out.mkdir(parents=True, exist_ok=True)
    log = []
    for view in args.views:
        started = time.time()
        try:
            if args.via == "relay":
                data, request_id = relay_request(*values, args.model, image_bytes, PROMPTS[view])
            else:
                image = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
                result = request(values[0], args.model, image, PROMPTS[view])
                url = result["output"]["choices"][0]["message"]["content"][0]["image"]
                with urllib.request.urlopen(url, timeout=120) as response:
                    data = response.read()
                request_id = result.get("request_id")
            (args.out / f"{view}.png").write_bytes(data)
            entry = {"view": view, "ok": True, "seconds": round(time.time() - started, 1), "request_id": request_id}
        except (RuntimeError, KeyError, IndexError, urllib.error.URLError) as exc:
            entry = {"view": view, "ok": False, "error": str(exc)}
        log.append({**entry, "model": args.model, "prompt": PROMPTS[view], "photo": args.photo.name})
        print(json.dumps({k: v for k, v in entry.items()}, ensure_ascii=False))
    (args.out / "generation.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
