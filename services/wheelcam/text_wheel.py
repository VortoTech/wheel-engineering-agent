"""A reviewed text proposal to a wheel mesh draft. Engineering dimensions come from text only."""
import argparse
import json
import math
import os
import re
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from . import mesh_build, recipe_chat
from .forged_blank import ForgedWheel, hole_form, recipe_from_dict, window_outlines
from .wheel_skill import KEY_SPECS, envelope_from_specs

PRESETS = Path(__file__).resolve().parents[2] / "experiments/forged-blank/recipes"
FAMILIES = {"single": "work6-tapered", "y_split": "hf6-y-split", "skeleton": "tree6-branching"}
PRESET_FAMILY = {"work6-tapered": "single", "wide6-centre-groove": "single",
                 "hf6-y-split": "y_split", "tree6-branching": "skeleton"}
DEFAULT_SPEC = {"diameter_in": 20, "width_in": 9.5, "pcd_mm": 112, "bolts": 5,
                "center_bore_mm": 66.6, "et_mm": 35}
SHAPE = {"stem_w_hub", "stem_w_split", "split_r", "arm_angle_deg", "arm_w", "arm_bow", "spoke_sweep_deg"}
SPEC_PATTERNS = {
    "et_mm": r"(?:\bET\s*\+?\s*|偏距\s*)(-?\d+(?:\.\d+)?)",
    "pcd_mm": r"(?:\bPCD\s*[:：]?\s*|\b[3-9]\s*[×xX*]\s*)(\d{2,3}(?:\.\d+)?)",
    "center_bore_mm": r"(?:\bCB\s*[:：]?\s*|中心孔\s*(?:直径|Ø|φ)?\s*)(\d+(?:\.\d+)?)",
    "diameter_in": r"(?:\b)(\d{2}(?:\.\d+)?)\s*(?:[×xX*]\s*\d+(?:\.\d+)?\s*(?:J\b)?|寸|英寸|[\"″])",
    "width_in": r"\b\d{2}(?:\.\d+)?\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*J?\b",
    "bolts": r"\b([3-9])\s*[×xX*]\s*\d{2,3}(?:\.\d+)?\b",
}


def specs_in_text(message: str) -> tuple[dict, str | None]:
    """Only explicit, labelled dimensions; no model value can enter this result."""
    spec = {}
    for key, pattern in SPEC_PATTERNS.items():
        m = re.search(pattern, message, re.I)
        if m:
            value = float(m.group(1))
            spec[key] = int(value) if key == "bolts" else value
    m = re.search(r"\b\d+(?:\.\d+)?\s*[Xx×*]\s*\d+(?:\.\d+)?\s*[Xx×*]\s*\d+(?:\.\d+)?\b", message)
    form = m.group(0).replace("×", "X").replace("*", "X").upper().replace(" ", "") if m else None
    return spec, form if form and hole_form(form) else None


def _json_answer(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        raise ValueError("模型没有返回 JSON 对象")
    answer = json.loads(content[start:end + 1])
    if not isinstance(answer, dict):
        raise ValueError("模型应返回 JSON 对象")
    return answer


def ask_model(message: str) -> dict:
    """Use the configured local OpenAI-compatible endpoint; one repair retry for malformed JSON."""
    import httpx
    base, model, key = recipe_chat._endpoint()
    system = ("你是轮毂造型提案助手，只返回 JSON 对象。字段：spec、hole_form、family、preset、spokes、shape、style、reply、unknown。"
              "family 只能 single/y_split/skeleton；preset 只能 work6-tapered/wide6-centre-groove/hf6-y-split/tree6-branching。"
              "shape 可用 stem_w_hub,stem_w_split,split_r,arm_angle_deg,arm_w,arm_bow,spoke_sweep_deg。"
              "style 可用 hub_z,flank_w,flank_depth,lip_pockets 等。不能做扭转辐或精确品牌复刻。"
              "spec 只提取用户文字明确给出的尺寸，未知项留空，不要猜。"
              '必须保留类型：spec、shape、style 是 JSON 对象；spokes 是整数；unknown 是数组。'
              '示例输出：{"spec":{"diameter_in":20,"width_in":10.5,"et_mm":15,"pcd_mm":112,"bolts":5,"center_bore_mm":66.6},'
              '"hole_form":"15X32X60","family":"single","preset":"work6-tapered","spokes":5,'
              '"shape":{"stem_w_hub":48},"style":{"hub_z":-85,"lip_pockets":15},"reply":"5 辐直辐草案","unknown":[]}')
    messages = [{"role": "system", "content": system}, {"role": "user", "content": message}]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 850,
            "response_format": {"type": "json_object"}, "chat_template_kwargs": {"enable_thinking": False}}
    for attempt in range(2):
        response = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=90)
        if response.status_code == 400 and "chat_template_kwargs" in body:
            body.pop("chat_template_kwargs"); body.pop("response_format")
            response = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=90)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"].get("content") or ""
        try:
            return _json_answer(content)
        except ValueError:
            if attempt:
                raise ValueError("模型两次未返回合法 JSON，未生成轮毂")
            body["messages"] = messages + [{"role": "assistant", "content": content},
                                          {"role": "user", "content": "请重新只输出一个完整、合法的 JSON 对象。"}]
    raise AssertionError("unreachable")


def _explicit_family(message: str) -> str | None:
    if re.search(r"[YyＹｙ]\s*(?:形|型)?\s*分叉|分叉辐", message):
        return "y_split"
    if re.search(r"直辐|直条辐|单辐", message):
        return "single"
    return None


def _spokes_in_text(message: str) -> int | None:
    m = re.search(r"(?<!\d)(\d{1,2})\s*(?:根|条|组)?\s*辐", message)
    return int(m.group(1)) if m else None


def _to_outline(recipe: dict) -> dict:
    p = recipe_from_dict(recipe)
    loops = [np.asarray(o) for o in window_outlines(p, samples=96)]
    pitch = 360 / p.spokes
    group = [o for o in loops if -pitch / 2 <= math.degrees(math.atan2(*o.mean(0)[::-1])) < pitch / 2]
    if not group:
        raise ValueError("参数化辐条没有产生窗口轮廓")
    outlines = [[[round(float(math.hypot(x, y)), 4), round(float(math.degrees(math.atan2(y, x))), 4)]
                 for x, y in o] for o in group]
    return {**recipe, "family": "outline", "outlines": outlines}


def review(message: str, proposal: dict) -> dict:
    """Deterministic gate: text owns specs and topology; the model may choose only bounded styling."""
    if re.search(r"扭转辐|旋转辐|涡轮辐|像.{0,20}(品牌|某款)|(?:品牌|某款).{0,20}(一样|同款)", message):
        raise ValueError("当前模板不能可靠生成扭转辐或精确品牌款式；请改为直辐或 Y 形分叉等可支持的造型。")
    proposal = proposal if isinstance(proposal, dict) else {}
    explicit, form = specs_in_text(message)
    spec = {**DEFAULT_SPEC, **explicit}
    rejected, clipped, questions = [], [], []
    model_spec = proposal.get("spec") or {}
    if not isinstance(model_spec, dict):
        rejected.append({"param": "spec", "value": str(model_spec)[:120], "reason": "模型规格不是对象，已只按原文解析"})
        model_spec = {}
    for key, value in model_spec.items():
        if key not in KEY_SPECS or key not in explicit or value != explicit[key]:
            rejected.append({"param": key, "value": value, "reason": "工程规格必须由原文确定"})
    if proposal.get("hole_form") and proposal["hole_form"] != form:
        rejected.append({"param": "hole_form", "value": proposal["hole_form"], "reason": "孔型必须来自原文"})
    for key in KEY_SPECS:
        if key not in explicit:
            questions.append(f"请确认 {key}；当前仅用默认值 {spec[key]} 建立视觉草案。")
    if not 16 <= spec["diameter_in"] <= 24 or not 6 <= spec["width_in"] <= 15:
        raise ValueError("直径或宽度超出当前模板支持范围")
    if not 3 <= spec["bolts"] <= 12 or not 80 <= spec["pcd_mm"] <= 180 or not 45 <= spec["center_bore_mm"] <= 120:
        raise ValueError("孔系超出当前模板支持范围")
    if not -30 <= spec["et_mm"] <= 75:
        raise ValueError("ET 超出当前模板支持范围")
    if form is None:
        questions.append("请确认螺栓孔型；当前使用模板孔型，仅作草案。")
    family = _explicit_family(message) or proposal.get("family", "y_split")
    if not isinstance(family, str) or family not in FAMILIES:
        rejected.append({"param": "family", "value": family, "reason": "未知辐条类型"})
        family = "y_split"
    requested_preset = proposal.get("preset")
    preset = requested_preset if isinstance(requested_preset, str) and PRESET_FAMILY.get(requested_preset) == family else FAMILIES[family]
    if requested_preset and preset != requested_preset:
        rejected.append({"param": "preset", "value": requested_preset, "reason": "预设与辐条类型不匹配"})
    explicit_spokes = _spokes_in_text(message)
    spokes = explicit_spokes if explicit_spokes is not None else proposal.get("spokes", 6)
    if explicit_spokes is None and isinstance(spokes, str) and spokes.isdecimal():
        spokes = int(spokes)
    if not isinstance(spokes, int) or not 3 <= spokes <= 12:
        raise ValueError("辐条数应为 3–12")
    if explicit_spokes is not None and proposal.get("spokes") not in (None, explicit_spokes):
        rejected.append({"param": "spokes", "value": proposal["spokes"], "reason": "以用户原文的辐条数为准"})
    if explicit_spokes is None:
        questions.append(f"请确认辐条数；当前按模型选择的 {spokes} 辐建视觉草案。")
    base = json.loads((PRESETS / f"{preset}.json").read_text())
    base = {k: v for k, v in base.items() if not k.startswith("_")}
    old = recipe_from_dict(base)
    env, _ = envelope_from_specs(spec)
    recipe = {**base, **env, "spokes": spokes, "lip_pockets": 0}
    scale = recipe["lip_r"] / old.lip_r
    recipe["window_r_in"] = round(max(recipe["hub_r"] + 12, old.window_r_in * scale), 1)
    recipe["window_r_out"] = round(min(recipe["ring_r"] - 4, old.window_r_out * scale), 1)
    recipe["split_r"] = round(max(recipe["window_r_in"] + 12, old.split_r * scale), 1)
    # The chosen concavity is styling; ET is held by deriving the web from the user/default spec.
    recipe["hub_z"] = -80.0 if "深" in message else -60.0
    shape_sources = {}
    model_shape = proposal.get("shape") or {}
    if not isinstance(model_shape, dict):
        rejected.append({"param": "shape", "reason": "造型参数不是对象"})
        model_shape = {}
    for key, value in model_shape.items():
        if key not in SHAPE or key == "spoke_sweep_deg" and value != 0:
            rejected.append({"param": key, "value": value, "reason": "不支持的辐条形态参数"})
            continue
        try:
            val = float(value)
        except (ValueError, TypeError):
            rejected.append({"param": key, "value": value, "reason": "应为数值"})
            continue
        if not math.isfinite(val):
            rejected.append({"param": key, "value": str(value), "reason": "应为有限数值"})
            continue
        initial = float(recipe.get(key, getattr(old, key)))
        margin = max(abs(initial) * .3, 12 if key != "arm_angle_deg" else 7)
        lo, hi = initial - margin, initial + margin
        if key.endswith("w") or key.startswith("stem_w"):
            lo = max(4, lo)
        if key in {"split_r"}:
            lo = max(recipe["window_r_in"] + 8, lo); hi = min(recipe["window_r_out"] - 8, hi)
        if lo > hi:
            rejected.append({"param": key, "value": value, "reason": "当前窗口没有可用范围"})
            continue
        accepted = round(min(max(val, lo), hi), 2)
        if accepted != val:
            clipped.append({"param": key, "requested": val, "used": accepted})
        recipe[key] = accepted
        shape_sources[key] = "model_choice"
    model_style = proposal.get("style") or {}
    if not isinstance(model_style, dict):
        rejected.append({"param": "style", "reason": "风格参数不是对象"})
        model_style = {}
    style_proposal = {"changes": [{"param": k, "value": v} for k, v in model_style.items()],
                      "refused": []}
    style_review = recipe_chat.review(recipe, style_proposal)
    rejected += style_review["refused"]
    clipped += [{"param": note.split()[0], "note": note} for note in style_review["notes"]]
    # A count explicitly requested by the user wins over a model omission or conflicting number.
    pocket_match = re.search(r"(?:外圈|轮缘).{0,8}?(\d{1,2})\s*(?:个|处)?\s*盲窗", message)
    if pocket_match:
        count = int(pocket_match.group(1))
        if count > 40:
            clipped.append({"param": "lip_pockets", "requested": count, "used": 40})
            count = 40
        if style_review["accepted"].get("lip_pockets") not in (None, count):
            rejected.append({"param": "lip_pockets", "value": style_review["accepted"]["lip_pockets"],
                             "reason": "以用户原文盲窗数量为准"})
        style_review["accepted"]["lip_pockets"] = count
    if style_review["accepted"]:
        recipe = recipe_chat.apply_edit(recipe, style_review["accepted"])
    # Explicit qualitative words are still actionable if the model omits or mangles a nested field.
    if re.search(r"辐条.{0,4}宽一点|辐条.{0,4}加宽", message):
        for key in ("stem_w_hub", "stem_w_split") if family == "single" else ("stem_w_hub", "arm_w"):
            recipe[key] = round(float(recipe[key]) * 1.2, 2)
            shape_sources[key] = "user"
    if "深" in message and recipe["hub_z"] > -80:
        recipe["hub_z"] = -80.0
    if "深" in message:
        shape_sources["hub_z"] = "user"
    recipe["web_thick_hub"] = round(recipe["hub_z"] + recipe["width"] / 2 - spec["et_mm"], 2)
    parsed_form = hole_form(form) if form else {}
    recipe.update(parsed_form)
    if form and recipe["seat_d"] >= spec["pcd_mm"] - spec["center_bore_mm"] - 6:
        raise ValueError("用户孔型与中心孔之间没有足够孔座壁厚，请核对 PCD/CB/孔型")
    recipe = _to_outline(asdict(recipe_from_dict(recipe)))
    provenance = {k: {"value": spec[k], "source": "user" if k in explicit else "default"} for k in KEY_SPECS}
    provenance["hole_form"] = {"value": form, "source": "user" if form else "default"}
    provenance["spokes"] = {"value": spokes, "source": "user" if explicit_spokes is not None else "model_choice"}
    provenance["family"] = {"value": family, "source": "user" if _explicit_family(message) else "model_choice"}
    provenance["preset"] = {"value": preset, "source": "model_choice"}
    provenance.update({k: {"value": recipe[k], "source": v} for k, v in shape_sources.items()})
    provenance.update({k: {"value": recipe[k], "source": "user" if k == "lip_pockets" and pocket_match else "model_choice"}
                       for k in style_review["accepted"]})
    if "深" in message:
        provenance["hub_z"] = {"value": recipe["hub_z"], "source": "user"}
    recipe_sources = {k: {"value": len(v) if k == "outlines" else v, "source": "default"}
                      for k, v in recipe.items()}
    recipe_sources["outlines"].update({"source": "model_choice", "note": "由参数化辐条生成的单组窗口轮廓数量"})
    for field, input_key in (("lip_r", "diameter_in"), ("lip_face_r_in", "diameter_in"),
                             ("barrel_outer_r", "diameter_in"), ("barrel_inner_r", "diameter_in"),
                             ("ring_r", "diameter_in"), ("width", "width_in"), ("pcd", "pcd_mm"),
                             ("bolts", "bolts"), ("center_bore_r", "center_bore_mm"),
                             ("web_thick_hub", "et_mm")):
        recipe_sources[field]["source"] = "user" if input_key in explicit else "default"
        recipe_sources[field]["note"] = f"由 {input_key} 和模板规则换算"
    for field in ("bolt_d", "seat_d", "seat_cone_deg"):
        if form:
            recipe_sources[field]["source"] = "user"
            recipe_sources[field]["note"] = "由用户孔型换算"
    for key in ("spokes", "lip_pockets", *shape_sources, *style_review["accepted"]):
        if key in provenance and key in recipe_sources:
            recipe_sources[key]["source"] = provenance[key]["source"]
    recipe_sources["family"] = {"value": "outline", "source": provenance["family"]["source"],
                                "note": f"由 {family} 辐条参数转换为窗口轮廓"}
    if recipe["lip_pockets"]:
        recipe_sources["lip_pocket_r"]["source"] = provenance.get("lip_pockets", {}).get("source", "default")
        recipe_sources["lip_pocket_r"]["note"] = "按盲窗数量选择轮缘带半径"
    if "hub_z" in provenance:
        recipe_sources["hub_z"]["source"] = provenance["hub_z"]["source"]
    summary = f"已生成 {spokes} 辐{'直辐' if family == 'single' else 'Y 形分叉' if family == 'y_split' else '骨架'}视觉草案。"
    if pocket_match:
        summary += f"外圈盲窗 {recipe['lip_pockets']} 个。"
    if questions:
        summary += f"有 {len(questions)} 项待确认。"
    unknown = [k for k in KEY_SPECS if k not in explicit]
    if not form:
        unknown.append("hole_form")
    return {"spec": spec, "hole_form": form, "recipe": recipe, "parameters": provenance,
            "recipe_parameters": recipe_sources,
            "questions": questions, "unknown": unknown, "rejected": rejected, "clipped": clipped,
            "reply": summary}


def run(message: str, out, ask=ask_model) -> dict:
    out = Path(out)
    if not message.strip():
        raise ValueError("请描述轮毂")
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"输出目录非空：{out}")
    proposal = ask(message)
    decision = review(message, proposal)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    body, build_report = mesh_build.build(decision["recipe"])
    build_seconds = time.monotonic() - t0
    checks = mesh_build.verify(body, recipe_from_dict(decision["recipe"]), decision["spec"])
    mesh_build.export_glb(body, out / "wheel.glb")
    from PIL import Image
    from .front_render import render_front
    Image.fromarray(render_front(body, size=320)).save(out / "front.png")
    (out / "recipe.json").write_text(json.dumps(decision["recipe"], ensure_ascii=False, indent=1))
    report = {k: v for k, v in decision.items() if k != "recipe"}
    report.update({"source": "text", "proposal": proposal, "checks": checks,
                   "model": os.getenv("WHEELCAM_CHAT_MODEL") or os.getenv("WHEELCAM_AGENT_MODEL"),
                   "all_checks_pass": all(c["pass"] for c in checks.values()),
                   "build_seconds": round(build_seconds, 2), "mesh_build": build_report,
                   "readiness": "L0", "manufacturing_status": "not_released",
                   "readiness_limits": ["仅网格视觉草案；未通过 STEP 回读，不能评为 L1。",
                                        "默认工程尺寸尚待用户确认。" if any(p["source"] == "default" for p in decision["parameters"].values()) else
                                        "几何检查不等于工程或制造批准。"]})
    (out / "engineering_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return report


def main():
    ap = argparse.ArgumentParser(description="文字生成可审核的轮毂网格草案")
    ap.add_argument("message")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    result = run(args.message, args.out)
    print(json.dumps({"out": str(args.out), "checks_pass": result["all_checks_pass"],
                      "build_seconds": result["build_seconds"], "questions": result["questions"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
