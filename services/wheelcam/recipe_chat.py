"""Conversational style edits of a forged-wheel recipe: words -> whitelisted parameters -> mesh rebuild.

    PYTHONPATH=services .venv/bin/python -m wheelcam.recipe_chat RECIPE.json "辐条脊线再高一点" --out DIR
        [--spec spec.json]

The language model only proposes a JSON edit; this module decides. Only STYLE parameters can
change, each within its range; the engineering fixes of the order (PCD, bolt count, centre bore,
rim diameter and width, ET, hole form, spoke count) are refused with the reason. An accepted edit is
rebuilt on the mesh kernel (seconds) and the same checks run again, so a change that breaks the
bolt pattern or ET is reported, not hidden.

Model: any OpenAI-compatible chat endpoint, WHEELCAM_CHAT_BASE_URL / _MODEL / _API_KEY (falling back
to WHEELCAM_AGENT_*). On Spark this is the local vLLM behind an SSH tunnel.
"""
import argparse
import json
import os
import re
from dataclasses import asdict, replace
from pathlib import Path

from .forged_blank import recipe_from_dict, seat_cone_height
from .machining_step import MIN_HOLE_LAND_MM

# name -> (Chinese label, min, max, what it changes). Only parameters that change the mesh build of the
# real orders: the ridge ignores spoke_pad_w, and the pocket style cut nothing on M59 (2026-09-27).
STYLE = {
    "hub_z": ("凹度（中心相对轮缘的深度，负值越小越深）", -140.0, -30.0, "整个辐条面的凹陷深度；安装面位置保持不变"),
    "concavity_exp": ("凹面曲线形状", .6, 2.2, "<1 靠近中心陡、>1 靠近外圈陡"),
    "flank_w": ("窗口侧斜面宽度 mm", 0.0, 24.0, "窗口边缘的斜面（倒角面）宽度"),
    "flank_depth": ("窗口侧斜面深度 mm", 0.0, 30.0, "窗口斜面向下的深度"),
    "spoke_pad_depth": ("辐条脊线高度 mm", 0.0, 16.0, "脊两侧向辐条边缘下降的高度"),
    "hub_valley_depth": ("中心凹谷深度 mm", 0.0, 25.0, "轮毂中心辐条根部之间的凹谷"),
    "hub_arm_w": ("中心辐条根部宽度 mm", 12.0, 45.0, "中心凹谷之间留下的辐条根部宽度"),
}
CHOICES = {}                 # name -> allowed strings (none at present)
# Fixed by the order or the photo: never changed by a conversation.
LOCKED = {
    "pcd": "PCD 来自确认单", "bolts": "螺栓孔数来自确认单", "center_bore_r": "中心孔来自确认单",
    "lip_r": "轮辋直径来自确认单", "width": "轮辋宽度来自确认单", "web_thick_hub": "ET 来自确认单（安装面位置）",
    "bolt_d": "孔型来自确认单", "seat_d": "孔型来自确认单", "seat_cone_deg": "孔型来自确认单",
    "spokes": "辐条组数来自照片轮廓，改组数需要重新识图", "outlines": "窗口轮廓来自照片，需重新描图",
}
NAMES = {"pcd": "PCD", "bolts": "螺栓孔数", "center_bore_r": "中心孔", "lip_r": "轮辋直径", "width": "轮辋宽度",
         "web_thick_hub": "ET", "bolt_d": "孔型", "seat_d": "孔型", "seat_cone_deg": "孔型", "spokes": "辐条组数",
         "outlines": "窗口轮廓", "hub_z": "凹度"}
ALIASES = {"et": "web_thick_hub", "et_mm": "web_thick_hub", "pcd_mm": "pcd", "center_bore_mm": "center_bore_r",
           "diameter_in": "lip_r", "width_in": "width", "hole_form": "bolt_d"}


def system_prompt(recipe: dict) -> str:
    rows = "\n".join(f"- {k}: {label}；范围 {lo}–{hi}；{what}；当前 {recipe.get(k)}" if lo is not None else
                     f"- {k}: {label}；可选 {'/'.join(CHOICES[k])}；{what}；当前 {recipe.get(k)}"
                     for k, (label, lo, hi, what) in STYLE.items())
    locked = "、".join(f"{k}（{why}）" for k, why in LOCKED.items())
    return ("你是锻造轮毂的造型助手。用户用自然语言描述想要的造型变化，你只输出一个 JSON 对象，不要其他文字：\n"
            '{"changes": [{"param": "<参数名>", "value": <数值或字符串>}], "reply": "<一句中文说明>", '
            '"refused": [{"param": "<参数名>", "reason": "<原因>"}]}\n'
            f"只能修改以下造型参数：\n{rows}\n"
            f"以下是工程约束，任何情况下都不能改，用户要求时放进 refused 并说明原因：{locked}。\n"
            "“高一点/深一点/宽一点”这类模糊说法，按当前值的 20–30% 调整并在 reply 里说明取值；超出范围就取边界值。"
            "听不懂或与造型无关时，changes 为空，在 reply 里追问。\n"
            "reply 只能描述 changes 里真正列出的修改；你在 reply 里说要调整的每个参数都必须出现在 changes 里。\n"
            '示例：用户说“PCD 改成 120，脊高一点” -> {"changes": [{"param": "spoke_pad_depth", "value": 12}], '
            '"reply": "脊线高度 10 → 12 mm；PCD 是确认单尺寸，不能修改。", '
            '"refused": [{"param": "pcd", "reason": "PCD 来自确认单"}]}')


def _endpoint():
    get = lambda k: os.getenv(f"WHEELCAM_CHAT_{k}") or os.getenv(f"WHEELCAM_AGENT_{k}")
    base, model = get("BASE_URL"), get("MODEL")
    if not base or not model:
        raise RuntimeError("未配置对话模型：设置 WHEELCAM_CHAT_BASE_URL 和 WHEELCAM_CHAT_MODEL。")
    return base.rstrip("/"), model, get("API_KEY")


def ask_model(recipe: dict, message: str, history=(), timeout=90) -> dict:
    """The model's proposal, parsed from its JSON answer (thinking disabled where the server allows)."""
    import httpx
    base, model, key = _endpoint()
    messages = [{"role": "system", "content": system_prompt(recipe)}, *history, {"role": "user", "content": message}]
    body = {"model": model, "messages": messages, "temperature": 0.1, "max_tokens": 600,
            "response_format": {"type": "json_object"},
            "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    if r.status_code == 400:                        # servers that reject the vLLM-only fields
        body.pop("chat_template_kwargs"), body.pop("response_format")
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"] or ""
    try:
        return parse_answer(text)
    except ValueError:                           # malformed JSON (json.JSONDecodeError is a ValueError): ask once more
        body["messages"] = messages + [{"role": "assistant", "content": text},
                                       {"role": "user", "content": "上面的回答不是合法 JSON。请只输出一个合法的 JSON 对象。"}]
        r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        return parse_answer(r.json()["choices"][0]["message"]["content"] or "")


def parse_answer(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("模型没有返回 JSON")
    data = json.loads(m.group(0))
    return {"changes": list(data.get("changes") or []), "reply": str(data.get("reply") or ""),
            "refused": list(data.get("refused") or [])}


def review(recipe: dict, proposal: dict) -> dict:
    """{accepted: {param: value}, refused: [{param, reason}], notes}: the whitelist and ranges decide,
    whatever the model said."""
    accepted, refused, notes = {}, list(proposal.get("refused") or []), []
    for change in proposal.get("changes") or []:
        name = ALIASES.get(str(change.get("param")), str(change.get("param")))
        value = change.get("value")
        if name in LOCKED:
            refused.append({"param": name, "reason": LOCKED[name] + "，对话不能修改"})
            continue
        if name not in STYLE:
            refused.append({"param": name, "reason": "不在可修改的造型参数里"})
            continue
        if name in CHOICES:
            if value not in CHOICES[name]:
                refused.append({"param": name, "reason": f"只能取 {'/'.join(CHOICES[name])}"})
                continue
            accepted[name] = value
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            refused.append({"param": name, "reason": f"取值 {value!r} 不是数字"})
            continue
        _, lo, hi, _ = STYLE[name]
        if not lo <= value <= hi:
            clipped = min(max(value, lo), hi)
            notes.append(f"{name} {value:g} 超出范围 {lo:g}–{hi:g}，取 {clipped:g}")
            value = clipped
        accepted[name] = round(value, 2)
    return {"accepted": accepted, "refused": refused, "notes": notes}


def apply_edit(recipe: dict, accepted: dict) -> dict:
    """New recipe with the accepted values. A deeper or shallower dish moves the hub front; the
    mounting face (ET) stays where the order puts it, so the hub web takes up the difference."""
    p = recipe_from_dict(recipe)
    new = dict(accepted)
    if "hub_z" in new:
        dz = new["hub_z"] - p.hub_z
        new["web_thick_hub"] = round(p.web_thick_hub + dz, 2)
        if p.hub_crease_r > p.hub_r:
            new["hub_crease_z"] = round(p.hub_crease_z + dz, 2)
        need = p.seat_depth + (seat_cone_height(p) if p.seat_cone_deg > 0 else 0) + MIN_HOLE_LAND_MM
        if new["web_thick_hub"] < need:
            raise ValueError(f"凹度 {new['hub_z']:g} 会让安装面处只剩 {new['web_thick_hub']:g} mm 厚，"
                             f"螺栓孔和锥座至少要 {need:.0f} mm，拒绝修改")
    if "spoke_pad_depth" in new and new["spoke_pad_depth"] > 0 and p.spoke_pad_w <= 0 and "spoke_pad_w" not in new:
        new["spoke_pad_w"] = 20.0                    # a ridge needs a top; the template's width
    out = asdict(replace(p, **new))
    return out


def rebuild(recipe: dict, out: Path, spec: dict | None = None) -> dict:
    """Mesh build + checks of an edited recipe; writes recipe.json, wheel.glb and report.json."""
    from . import mesh_build
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    body, report = mesh_build.build(recipe)
    p = recipe_from_dict(recipe)
    checks = mesh_build.verify(body, p, spec or {})
    mesh_build.export_glb(body, out / "wheel.glb")
    (out / "recipe.json").write_text(json.dumps(recipe, ensure_ascii=False, indent=1))
    result = {"checks": checks, "passed": all(c["pass"] for c in checks.values()),
              "volume_mm3": report["volume_mm3"], "mass_kg_6061": report["mass_kg_6061"],
              "seconds": report["seconds"], "status": "not_released"}
    (out / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result


def turn(recipe: dict, message: str, out, spec=None, history=(), ask=ask_model) -> dict:
    """One conversation turn: proposal -> review -> edit -> rebuild. Nothing is built when nothing
    was accepted."""
    proposal = ask(recipe, message, history)
    decision = review(recipe, proposal)
    result = {"message": message, "reply": proposal["reply"], **decision, "built": None}
    if decision["accepted"]:
        try:
            edited = apply_edit(recipe, decision["accepted"])
        except ValueError as e:                  # only the dish depth can fail here: keep the rest
            result["refused"].append({"param": "hub_z", "reason": str(e)})
            result["accepted"] = {k: v for k, v in decision["accepted"].items() if k != "hub_z"}
            if not result["accepted"]:
                result["summary"] = summary(result)
                return result
            edited = apply_edit(recipe, result["accepted"])
        result["built"] = rebuild(edited, out, spec)
        norm = lambda v: json.loads(json.dumps(v))           # tuples and lists compare equal
        before = asdict(recipe_from_dict(recipe))             # template defaults for missing keys
        result["changed"] = {k: {"from": before[k], "to": edited[k]} for k in result["accepted"]
                             if norm(edited[k]) != norm(before[k])}
        result["recipe"] = edited
    result["summary"] = summary(result)
    return result


def summary(result: dict) -> str:
    """What was actually done, in words, from the decision (the model's own reply may promise more)."""
    parts = [f"{STYLE[k][0].split('（')[0].split(' mm')[0]} {v['from']:g} → {v['to']:g}"
             for k, v in (result.get("changed") or {}).items()]
    text = "已修改：" + "；".join(parts) + "。" if parts else ""
    if result.get("refused"):
        text += "未修改：" + "；".join(f"{NAMES.get(r['param'], r['param'])}（{r['reason']}）"
                                     for r in result["refused"]) + "。"
    built = result.get("built")
    if built:
        failed = [k for k, c in built["checks"].items() if not c["pass"]]
        text += "重建后检查全部通过。" if not failed else f"重建后检查未通过：{'、'.join(failed)}。"
    return text or result.get("reply") or "没有可执行的修改。"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recipe")
    ap.add_argument("message")
    ap.add_argument("--out", required=True)
    ap.add_argument("--spec", help="spec.json of the order: the checks use its dimensions")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())["spec"] if a.spec else None
    result = turn(json.loads(Path(a.recipe).read_text()), a.message, a.out, spec)
    result.pop("recipe", None)
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
