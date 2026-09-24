"""Stable Fast 3D adapter.

The model runs in its own checkout/virtualenv because its PyTorch stack should not
be mixed with WheelCAM's CAD environment. Its mesh is visual evidence only.
"""
import hashlib
import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image


PROVIDER = "stable-fast-3d"


def settings():
    root_value = os.getenv("WHEELCAM_SF3D_ROOT", "").strip()
    python_value = os.getenv("WHEELCAM_SF3D_PYTHON", "").strip()
    root = Path(root_value).expanduser().resolve() if root_value else None
    # Keep the venv launcher path intact. Resolving its symlink can bypass the
    # virtual environment and invoke the base interpreter without SF3D deps.
    python = Path(python_value).expanduser().absolute() if python_value else None
    return {
        "root": root,
        "python": python,
        "device": os.getenv("WHEELCAM_SF3D_DEVICE", "mps").strip() or "mps",
        "model": os.getenv("WHEELCAM_SF3D_MODEL", "stabilityai/stable-fast-3d").strip(),
        "texture_resolution": int(os.getenv("WHEELCAM_SF3D_TEXTURE_RESOLUTION", "1024")),
        "timeout": int(os.getenv("WHEELCAM_SF3D_TIMEOUT", "1800")),
    }


def status():
    config = settings()
    reason = None
    if config["root"] is None or config["python"] is None:
        reason = "尚未配置 WHEELCAM_SF3D_ROOT 和 WHEELCAM_SF3D_PYTHON。"
    elif not (config["root"] / "run.py").is_file():
        reason = "Stable Fast 3D 目录中缺少 run.py。"
    elif not config["python"].is_file():
        reason = "Stable Fast 3D 独立 Python 不存在。"
    return {
        "provider": PROVIDER,
        "available": reason is None,
        "reason": reason,
        "device": config["device"],
        "model": config["model"],
        "output": "textured_glb",
        "usage": "visual_reference_only",
        "license": "Stability AI Community License；商用前需自行确认资格、注册与归属要求。",
    }


def command(image: Path, output_dir: Path):
    config = settings()
    state = status()
    if not state["available"]:
        raise ValueError(state["reason"])
    return [
        str(config["python"]), str(config["root"] / "run.py"), str(image),
        "--output-dir", str(output_dir), "--device", config["device"],
        "--pretrained-model", config["model"],
        "--texture-resolution", str(config["texture_resolution"]),
        "--remesh_option", "none",
    ], config


def prepare_input(source: Path, target: Path):
    """Preserve openings when a product photo uses a near-white studio background.

    Generic foreground matting often treats the white visible through wheel
    openings as part of the object and SF3D then reconstructs a closed disc.
    Only apply this path when most border pixels are neutral white; otherwise
    leave the source untouched and let the upstream remover handle it.
    """
    image = Image.open(source).convert("RGB")
    rgb = np.asarray(image, dtype=np.float32)
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    border_min = border.min(axis=1)
    border_chroma = border.max(axis=1) - border_min
    white_border_ratio = float(np.mean((border_min >= 245) & (border_chroma <= 12)))
    if white_border_ratio < 0.8:
        return source, {"mode": "upstream_background_removal", "white_border_ratio": round(white_border_ratio, 4)}

    minimum = rgb.min(axis=2)
    chroma = rgb.max(axis=2) - minimum
    white_distance = 255.0 - minimum
    opacity_from_value = np.clip((white_distance - 4.0) / 24.0, 0.0, 1.0)
    opacity_from_color = np.clip((chroma + white_distance - 6.0) / 20.0, 0.0, 1.0)
    alpha = (np.maximum(opacity_from_value, opacity_from_color) * 255).astype(np.uint8)
    rgba = np.dstack((rgb.astype(np.uint8), alpha))
    target.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(target)
    return target, {
        "mode": "white_background_alpha",
        "white_border_ratio": round(white_border_ratio, 4),
        "transparent_pixels": int(np.count_nonzero(alpha == 0)),
        "partial_pixels": int(np.count_nonzero((alpha > 0) & (alpha < 255))),
    }


def collect(output_dir: Path):
    generated = output_dir / "0" / "mesh.glb"
    if not generated.is_file() or generated.stat().st_size == 0:
        raise ValueError("Stable Fast 3D 未生成有效的 mesh.glb；请查看任务日志。")
    target = output_dir / "reference.glb"
    shutil.copyfile(generated, target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return target, {"sha256": digest, "bytes": target.stat().st_size}
