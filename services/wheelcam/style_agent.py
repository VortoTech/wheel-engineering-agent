"""Style agent: corrects the style assumptions of the skill's reconstruction against the order photo,
through the same whitelist as the conversation (recipe_chat).

    PYTHONPATH=services .venv/bin/python -m wheelcam.style_agent RECIPE.json FRONT.jpg --out DIR [--spec spec.json]

The skill's default styling (forged_y preset) is an assumption, not an observation; on M59 it gave
narrow spokes (16 mm window flanks) and no lip windows. The agent splits the work by what each tool
does well (2026-09-28, step3-vl-10b on Spark):
  look    whether a feature is there: the vision model answers yes/no (lip windows 6/6 right when
          made to answer at once); how many is counted from the photo, not by the model
  search  how big: candidates are rendered and scored against the photo edges (visual_check);
          asked whether the model's spokes were narrower than the photo's, the vision model said no
          with p 0.999 when they were
  verify  the edits pass the conversation whitelist, the part is rebuilt and checked again, and an
          edit that fails a check is undone
style_agent.json keeps every question, answer, score table and edit; before/after renders sit beside
it. Model: WHEELCAM_VLM_BASE_URL / _MODEL / _API_KEY, falling back to WHEELCAM_CHAT_*.
"""
import argparse
import base64
import io
import json
import math
import os
import re
from pathlib import Path

import numpy as np

from dataclasses import asdict

from .forged_blank import recipe_from_dict
from .front_render import render_front


QUESTIONS = {
    "spokes_narrower": (
        "图 A 是一个汽车轮毂的照片，图 B 是同一个轮毂的灰色三维模型，都是正面视角。比较辐条的正面宽度："
        "B 的辐条是否明显比 A 的辐条窄（B 细、A 宽）？只输出 JSON：{\"b_spokes_narrower\": true 或 false}",
        "b_spokes_narrower"),
    "lip_windows": (
        "图中是一个汽车轮毂的正面。辐条末端和最外圈轮缘之间有一圈环形区域。这圈环形区域上，是否有一排多个小的长方形凹槽或小窗口"
        "（比辐条之间的大开口小得多）？只输出 JSON：{\"small_pockets_in_outer_ring\": true 或 false}",
        "small_pockets_in_outer_ring"),
}


def _endpoint():
    get = lambda k: os.getenv(f"WHEELCAM_VLM_{k}") or os.getenv(f"WHEELCAM_CHAT_{k}")
    base, model = get("BASE_URL"), get("MODEL")
    if not base or not model:
        raise RuntimeError("未配置视觉模型：设置 WHEELCAM_VLM_BASE_URL 和 WHEELCAM_VLM_MODEL。")
    return base.rstrip("/"), model, get("API_KEY")


def _data_url(img, side=640):
    from PIL import Image
    im = img if isinstance(img, Image.Image) else Image.fromarray(np.asarray(img))
    im = im.convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def ask_vlm(images, question, key, timeout=120):
    """(answer, p_true) for a yes/no question. The reply is started for the model as the end of its
    thinking and the JSON key, so it answers true/false at once: left to think, step3-vl spent 2500
    tokens without answering on the wheels that have lip windows, and answered 6/6 lip-window
    questions right this way (2026-09-28). p_true comes from the first token's log probabilities."""
    import httpx
    base, model, api_key = _endpoint()
    content = [{"type": "image_url", "image_url": {"url": _data_url(im)}} for im in images]
    content.append({"type": "text", "text": question})
    body = {"model": model, "temperature": 0, "max_tokens": 8, "logprobs": True, "top_logprobs": 5,
            "messages": [{"role": "user", "content": content},
                         {"role": "assistant", "content": f'</think>\n{{"{key}": '}],
            "continue_final_message": True, "add_generation_prompt": False}
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    if r.status_code == 400:                 # step-5-preview rejects log probabilities with images (2026-09-29)
        body.pop("logprobs"), body.pop("top_logprobs")
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    choice = r.json()["choices"][0]
    text = (choice["message"]["content"] or "").strip().lower()
    if not (text.startswith("true") or text.startswith("false")):
        return _ask_plain(base, headers, model, content, key, text, timeout)
    probs = {}
    for token in (choice.get("logprobs") or {}).get("content", []):   # the first true/false token, not a space
        if token["token"].strip().lower() in ("true", "false"):
            for t in token.get("top_logprobs", []):   # " true" and "true" both count as true
                key_ = t["token"].strip().lower()
                probs[key_] = probs.get(key_, 0.0) + math.exp(t["logprob"])
            break
    if not probs:                            # the server returned no log probabilities
        return text.startswith("true"), None
    p_true = probs.get("true", 0.0) / max(probs.get("true", 0.0) + probs.get("false", 0.0), 1e-9)
    return text.startswith("true"), round(p_true, 3)


def _ask_plain(base, headers, model, content, key, prefilled, timeout):
    """(answer, None) from a plain JSON answer, for servers that ignore the continued assistant turn
    (hosted APIs such as step-5-preview and step-3.7-flash, 2026-09-29): no log probabilities there."""
    from .recipe_chat import answer_budget, completion
    body = {"model": model, "temperature": 0, "max_tokens": answer_budget(2000),
            "messages": [{"role": "user", "content": content}], "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}
    text = completion(base, headers, body, max(timeout, 180))
    m = re.search(rf'"{re.escape(key)}"\s*:\s*(true|false)', text, flags=re.I)
    if not m:
        raise ValueError(f"视觉模型的回答不是 true/false：{(prefilled or text)[:80]!r}")
    return m.group(1).lower() == "true", None


def wheel_crop(photo):
    """The wheel's square from the front photo, and its centre and radius in pixels."""
    from PIL import Image
    from .wheel_skill import _front_rim_hub
    img = Image.open(photo).convert("RGB")
    a = np.asarray(img, float) / 255
    found = _front_rim_hub(a)
    if not found:
        raise ValueError("正面图里没有找到轮缘")
    rim, centre = found
    radius = float(np.mean(np.linalg.norm(np.asarray(rim) - centre, axis=1)))
    box = [int(centre[0] - radius * 1.02), int(centre[1] - radius * 1.02),
           int(centre[0] + radius * 1.02), int(centre[1] + radius * 1.02)]
    return img.crop(box), a, centre, radius


def lip_window_count(gray, centre, radius, groups):
    """Lip windows round the front photo's lip band: the strongest angular period that is a multiple
    of the spoke group count (case-04: 3 over each group's two windows, not a multiple of the windows). Only asked for once the vision model has said the photo has them
    (on its own this also counted the spokes of wheels without any, 2026-09-27)."""
    th = np.linspace(0, 2 * np.pi, 2048, endpoint=False)
    rows = []
    for share in np.linspace(.84, .92, 9):
        x = np.clip((centre[0] + share * radius * np.cos(th)).astype(int), 0, gray.shape[1] - 1)
        y = np.clip((centre[1] + share * radius * np.sin(th)).astype(int), 0, gray.shape[0] - 1)
        rows.append(gray[y, x])
    prof = np.mean(rows, axis=0)
    spec = np.abs(np.fft.rfft(prof - prof.mean()))
    multiples = [m for m in range(2 * groups, 49, groups)]
    return max(multiples, key=lambda m: spec[m]) if multiples else 0


FLANK_CANDIDATES = ((8.0, 11.0), (4.0, 6.0), (0.0, 0.0))   # window flank width, depth, mm
MIN_GAIN_MM = .1        # an edge-score gain below this is noise


def edge_score(recipe, photo_arr, centre, radius):
    """visual_check.compare_front of the recipe's mesh build: mean distance between photo edges and
    model crease edges on the spoke band, mm (lower is closer)."""
    from . import mesh_build
    from .visual_check import compare_front
    body, _ = mesh_build.build(recipe)
    scores, _ = compare_front(body, photo_arr, tuple(centre), radius, recipe_from_dict(recipe))
    return scores


def run(recipe, photo, out, spec=None, ask=ask_vlm):
    """The corrected recipe and the agent's log (also written to out/style_agent.json).

    look    the vision model says whether the photo has lip windows (yes/no, which it gets right;
            asked how wide the spokes are it was wrong, 2026-09-28); their count is read from the
            photo's lip band once it has said yes
    search  the window flank (the forged-Y preset's 16 mm made M59's spokes thin) is chosen among
            candidates by the edge distance between the photo and a render of each
    verify  the accepted edits go through the conversation whitelist and the checks run again; an
            edit that fails a check is undone"""
    from PIL import Image
    from . import mesh_build
    from .recipe_chat import apply_edit, review
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    photo_sq, photo_arr, centre, radius = wheel_crop(photo)
    photo_sq.save(out / "photo.jpg")
    p = recipe_from_dict(recipe)
    body, _ = mesh_build.build(recipe)
    Image.fromarray(render_front(body)).save(out / "before_render.png")
    log = {"photo": str(photo), "status": "not_released", "steps": [],
           "note": "视觉模型只回答特征有无（是/否）；尺寸用渲染与照片的边缘对比搜索；每项修改走对话白名单并重跑校验。"}
    changes = []
    # look: discrete features
    lip, p_lip = ask([photo_sq], *QUESTIONS["lip_windows"])
    step = {"step": "look", "question": "照片外圈是否有一圈小盲窗", "answer": lip, "p_true": p_lip,
            "model_has": p.lip_pockets > 0}
    if lip and p.lip_pockets <= 0:
        count = lip_window_count(photo_arr.mean(axis=2), centre, radius, p.spokes)
        changes.append({"param": "lip_pockets", "value": count})
        step["count"] = {"value": count, "method": "外圈环带的角向周期，取辐条组数的倍数"}
    elif not lip and p.lip_pockets > 0:
        changes.append({"param": "lip_pockets", "value": 0})
    log["steps"].append(step)
    # search: continuous style
    base = edge_score(recipe, photo_arr, centre, radius)
    table = [{"flank_w": p.flank_w, "flank_depth": p.flank_depth, "edge_mm": base["edge_mm"]}]
    for w, d in FLANK_CANDIDATES:
        if (w, d) != (p.flank_w, p.flank_depth):
            sc = edge_score({**recipe, "flank_w": w, "flank_depth": d}, photo_arr, centre, radius)
            table.append({"flank_w": w, "flank_depth": d, "edge_mm": sc["edge_mm"]})
    best = min(table, key=lambda row: row["edge_mm"])
    step = {"step": "search", "param": "窗口侧斜面", "scores": table, "window_iou": base["window_iou"]}
    if base["edge_mm"] - best["edge_mm"] > MIN_GAIN_MM:
        changes += [{"param": "flank_w", "value": best["flank_w"]}, {"param": "flank_depth", "value": best["flank_depth"]}]
        step["chosen"] = best
    log["steps"].append(step)
    # verify
    current = recipe
    if changes:
        decision = review(recipe, {"changes": changes})
        edited = apply_edit(recipe, decision["accepted"])
        new_body, _ = mesh_build.build(edited)
        checks = mesh_build.verify(new_body, recipe_from_dict(edited), spec or {})
        failed = [k for k, c in checks.items() if not c["pass"]]
        log["steps"].append({"step": "verify", "accepted": decision["accepted"], "refused": decision["refused"],
                             "checks_failed": failed,
                             "result": "已采用" if not failed else "校验未通过，已撤回"})
        if not failed:
            current, body = edited, new_body
    Image.fromarray(render_front(body)).save(out / "after_render.png")
    mesh_build.export_glb(body, out / "wheel.glb")
    (out / "recipe.json").write_text(json.dumps(current, ensure_ascii=False, indent=1))
    before = asdict(p)
    after = asdict(recipe_from_dict(current))
    log["changed"] = {k: {"from": before[k], "to": after[k]} for k in after
                      if k != "outlines" and json.dumps(after[k], default=list) != json.dumps(before[k], default=list)}
    (out / "style_agent.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
    return current, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe")
    ap.add_argument("front")
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="spec.json of the order: the checks use its dimensions")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())["spec"] if a.spec else None
    _, log = run(json.loads(Path(a.recipe).read_text()), a.front, a.out, spec)
    print(json.dumps({k: log[k] for k in ("steps", "changed")}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
