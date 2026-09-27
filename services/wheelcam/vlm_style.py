"""Style features of a wheel photo from a vision-language model (OpenAI-compatible chat endpoint).

Only yes/no feature questions: on eleven wheel photos Qwen3.8-27B with thinking off answered those
mostly right (arm groove 8/8, fork triangle 8/8, centre pad 7/8) and counted spokes and lugs wrong on
nearly every one (2026-09-26), so counts stay with the photo geometry.

    WHEELCAM_VLM_URL=http://127.0.0.1:8011/v1  WHEELCAM_VLM_MODEL=qwen3.8-27b-fp8
"""
import base64
import io
import json
import os
import re
import urllib.request

PROMPT = (
    "You are inspecting a product photo of ONE car wheel. Look closely at the spokes and the hub.\n"
    "Answer ONLY with JSON, no other text:\n"
    '{"center_pad": <true if the middle of each spoke has a raised flat strip standing above the spoke sides>,\n'
    ' "arm_groove": <true if spokes or spoke arms have a thin groove/channel line running along them>,\n'
    ' "hub_valleys": <true if the hub between the spoke roots is sunk into recessed pockets around the lug holes>}'
)
KEYS = ("center_pad", "arm_groove", "hub_valleys")


def configured() -> bool:
    return bool(os.getenv("WHEELCAM_VLM_URL") and os.getenv("WHEELCAM_VLM_MODEL"))


def _jpeg_b64(image) -> str:
    """`image`: a path or an (h, w, 3) float array in 0..1."""
    from PIL import Image
    import numpy as np
    im = Image.open(image) if isinstance(image, (str, os.PathLike)) else Image.fromarray((np.clip(image, 0, 1) * 255).astype("uint8"))
    im = im.convert("RGB")
    im.thumbnail((1280, 1280))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def detect(images, url=None, model=None, timeout=180) -> dict:
    """{center_pad, arm_groove, hub_valleys: bool, "source": "vlm", "model": ...} from one or more photos
    of the same wheel (a feature counts when any photo shows it). Raises on a missing or bad answer."""
    url = (url or os.getenv("WHEELCAM_VLM_URL", "")).rstrip("/")
    model = model or os.getenv("WHEELCAM_VLM_MODEL", "")
    if not url or not model:
        raise ValueError("vision model not configured (WHEELCAM_VLM_URL, WHEELCAM_VLM_MODEL)")
    found = {k: False for k in KEYS}
    for image in images:
        body = {"model": model, "temperature": 0, "max_tokens": 400,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + _jpeg_b64(image)}},
                    {"type": "text", "text": PROMPT}]}]}
        req = urllib.request.Request(url + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        text = json.loads(urllib.request.urlopen(req, timeout=timeout).read())["choices"][0]["message"].get("content") or ""
        match = re.findall(r"\{[^{}]*\}", text)
        if not match:
            raise ValueError(f"vision model gave no JSON: {text[-200:]!r}")
        answer = json.loads(match[-1])
        for k in KEYS:
            if not isinstance(answer.get(k), bool):
                raise ValueError(f"vision model answer lacks {k}: {answer}")
            found[k] = found[k] or answer[k]
    return {**found, "source": "vlm", "model": model}
