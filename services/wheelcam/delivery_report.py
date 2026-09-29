"""One report for one chain run, written from the run's own files (no model, no estimates).

    PYTHONPATH=services python -m wheelcam.delivery_report RUN_DIR      # -> RUN_DIR/REPORT.md, report_summary.json

An agent using the Wheel Engineering Skill relays this report instead of assembling its own summary
from five JSON files: every number, source and check below is read from chain.json,
reconstruct/engineering_report.json, machining/machining_report.json, package/process_plan.json,
compare.json and delivery_manifest.json as they are on disk.
"""
import argparse
import json
import os
import platform
from datetime import datetime
from pathlib import Path

SPECS = [("diameter_in", "直径 inch"), ("width_in", "宽度 J"), ("et_mm", "ET mm"), ("pcd_mm", "PCD mm"),
         ("bolts", "螺栓孔数"), ("center_bore_mm", "中心孔 mm")]
SOURCE = {"user": "用户提供", "drawing": "图纸/确认单", "measurement": "测量", "default": "默认值（待确认）",
          "unspecified": "来源未说明", "catalog": "目录参考", "spec": "输入规格", "photo": "照片观察",
          "estimate": "模板估计", "rule": "规则推导", "model_choice": "模型选择", "agent": "Agent 修改"}
GROUPS = {"supplied": "提供", "observed": "照片观察", "estimated": "模板估计", "defaulted": "默认", "unknown": "未知"}
CHECK = {"single_valid_solid": "单一有效实体", "valid_single_solid": "单一有效实体", "outer_diameter": "外径",
         "overall_width": "总宽", "bolt_pattern": "孔系", "offset_et": "ET", "rotational_symmetry": "旋转对称",
         "step_roundtrip": "STEP 回读", "no_sliver_faces": "无碎面", "hub_web_thickness": "中心盘厚度",
         "independent_lip_pockets": "独立盲窗"}


def _read(path):
    return json.loads(path.read_text()) if path.exists() else None


def _mark(ok):
    return "通过" if ok is True else "**未通过**" if ok is False else "—"


def _value(check):
    for key in ("measured_mm", "derived_mm", "holes_found", "faces_under_5mm2", "tool_components"):
        if key in check:
            return check[key]
    if "relative_volume_delta" in check:
        return f"体积相对差 {check['relative_volume_delta']:.1e}"
    return ""


def summarize(run: Path) -> dict:
    chain = _read(run / "chain.json") or {}
    eng = _read(run / "reconstruct/engineering_report.json") or {}
    mach = _read(run / "machining/machining_report.json") or {}
    plan = _read(run / "package/process_plan.json") or {}
    cmp = _read(run / "compare.json")
    manifest = _read(run / "delivery_manifest.json") or {}
    order = _read(run / "input/spec.json") or {}
    params = eng.get("parameters", {})
    evidence = (eng.get("engineering_understanding") or {}).get("spec_evidence") or order.get("spec_evidence") or {}
    specs = []
    for key, label in SPECS:
        value = (chain.get("spec") or {}).get(key)
        if chain.get("text"):                     # text runs record user/default per spec in their parameters
            source = (params.get(key) or {}).get("source")
        else:                                     # photo runs: the caller's evidence map is the source of record
            source = (evidence.get(key) or {}).get("source") or ("unspecified" if value is not None else None)
        specs.append({"key": key, "label": label, "value": value, "source": source,
                      "reference": (evidence.get(key) or {}).get("reference")})
    steps = chain.get("steps", [])
    questions = list(eng.get("questions") or [])
    for adj in mach.get("adjustments") or []:
        questions.append(f"加工调整：{adj}")
    for adj in eng.get("adjustments") or []:
        questions.append(f"建模调整：{adj.get('param')} {adj.get('before')} → {adj.get('after')}（{adj.get('reason', '')}）")
    return {
        "run": run.name, "generated": datetime.now().isoformat(timespec="seconds"),
        "device": {"system": platform.system(), "machine": platform.machine(), "python": platform.python_version()},
        "models": {"chat": os.getenv("WHEELCAM_CHAT_MODEL") or os.getenv("WHEELCAM_AGENT_MODEL"),
                   "vision": os.getenv("WHEELCAM_VLM_MODEL")},
        "input": "text" if chain.get("text") else "snapshot" if chain.get("parent") else "photo",
        "text": chain.get("text"), "steps": steps,
        "steps_ok": sum(bool(s.get("ok")) for s in steps), "seconds": chain.get("seconds"),
        "specs": specs, "hole_form": chain.get("hole_form"),
        "groups": (eng.get("engineering_understanding") or {}).get("evidence_groups") or {},
        "unknown": eng.get("unknown") or {}, "questions": questions,
        "rejected": eng.get("rejected") or [], "style_template": eng.get("style_template"),
        "style_agent": {"status": eng.get("style_agent_status"), "changed": eng.get("style_agent")},
        "mesh_checks": eng.get("checks") or {}, "machining_checks": mach.get("checks") or {},
        "in_step": mach.get("in_step") or [], "not_in_step": mach.get("not_in_step") or [],
        "simulation": plan.get("simulation") or {}, "compare": cmp,
        "pdf": (run / "drawing.pdf").exists(),
        "readiness": eng.get("readiness"), "manufacturing_status": chain.get("manufacturing_status"),
        "complete": manifest.get("complete"),
        "artifacts": {k: v.get("sha256", "")[:12] for k, v in (manifest.get("artifacts") or {}).items()
                      if k.endswith((".step", ".glb", ".svg", ".pdf", ".nc", ".csv", "recipe.json"))},
    }


def markdown(s: dict) -> str:
    L = [f"# 交付报告：{s['run']}", ""]
    status = "全部通过" if s["steps"] and s["steps_ok"] == len(s["steps"]) else f"{s['steps_ok']}/{len(s['steps'])} 步通过"
    L += [f"**结论**：链路 {status}（{s['seconds']} s）；准备等级 {s['readiness']}；"
          f"制造状态 `{s['manufacturing_status']}`；待确认 {len(s['questions'])} 项。所有产物都是待工程师审核的草案。", ""]
    models = s["models"]
    L += [f"- 输入：{ {'photo': '图片 + 规格', 'text': '文字', 'snapshot': '已确认配方快照'}[s['input']] }"
          + (f"：“{s['text']}”" if s["text"] else ""),
          f"- 执行设备：{s['device']['system']} {s['device']['machine']}，Python {s['device']['python']}（CAD 在此设备运行）",
          f"- 模型：文字/对话 {models['chat'] or '未配置'}；看图 {models['vision'] or '未配置'}",
          f"- 生成时间：{s['generated']}", ""]
    L += ["## 工程规格与来源", "", "| 规格 | 值 | 来源 |", "|---|---:|---|"]
    for sp in s["specs"]:
        src = SOURCE.get(sp["source"], sp["source"] or "—") + (f"（{sp['reference']}）" if sp.get("reference") else "")
        L.append(f"| {sp['label']} | {sp['value'] if sp['value'] is not None else '未知'} | {src} |")
    form_source = ("用户提供（原文）" if s["input"] == "text" else "确认单/用户") if s["hole_form"] else "默认值（待确认）"
    L += [f"| 孔型 | {s['hole_form'] or '未给（模板孔，待确认）'} | {form_source} |", ""]
    if s["groups"]:
        L += ["## 理解：哪些是看到的，哪些是假设", ""]
        for key, label in GROUPS.items():
            if s["groups"].get(key):
                L.append(f"- **{label}**：{'、'.join(sorted(s['groups'][key]))}")
        L.append("")
    if s["unknown"]:
        items = s["unknown"].items() if isinstance(s["unknown"], dict) else ((u, u) for u in s["unknown"])
        L += ["**照片无法确定**：" + "；".join((v if isinstance(v, str) else k).rstrip("。") for k, v in items) + "。", ""]
    L += ["## 需要确认的事项", ""]
    L += [f"{i}. {q}" for i, q in enumerate(s["questions"], 1)] or ["无。"]
    if s["rejected"]:
        L += ["", "模型提议中被审核拒绝的项：" + "、".join(str(r.get("param")) for r in s["rejected"])]
    L.append("")
    if s["style_agent"]["status"] or s["style_template"]:
        L += ["## 造型", ""]
        if s["style_template"]:
            t = s["style_template"]
            L.append(f"- 模板：{t.get('id')}（{'私有造型库' if t.get('kind') == 'private_outline' else '公开参数模板'}"
                     f"{'，实验造型' if t.get('experimental') else ''}）")
        if s["style_agent"]["status"]:
            changed = s["style_agent"]["changed"] or {}
            L.append(f"- 造型 Agent：{s['style_agent']['status']}；修改："
                     + ("、".join(f"{k} {v['from']}→{v['to']}" for k, v in changed.items() if isinstance(v, dict)) or "无"))
        L.append("")
    L += ["## 校验", "", "| 对象 | 检查 | 结果 | 实测 |", "|---|---|---|---|"]
    for obj, checks in (("网格预览", s["mesh_checks"]), ("加工级 STEP", s["machining_checks"])):
        for k, c in checks.items():
            L.append(f"| {obj} | {CHECK.get(k, k)} | {_mark(c.get('pass'))} | {_value(c)} |")
    L += ["", "网格预览的 ET 与加工级 STEP 的 ET 由建模参数推出，不是在 STEP 上独立测量。", ""]
    sim = s["simulation"]
    if sim:
        L += [f"**采样去料仿真**：{sim.get('conclusion', sim.get('status'))}（{sim.get('grid_mm')} mm 栅格采样；不含刀柄、夹具、机床碰撞）", ""]
    if s["compare"]:
        dims = s["compare"].get("dimensions", {})
        L += ["## 与工厂 CAD 对照", "", "| 尺寸 | 误差 |", "|---|---:|"]
        L += [f"| {k} | {v.get('error')} |" for k, v in dims.items()]
        rim = s["compare"].get("section_rim", {})
        L += [f"| 轮辋截面中位 / P90 mm | {rim.get('median_mm')} / {rim.get('p90_mm')} |", ""]
    L += ["## 交付物", "", f"工程图 PDF：{'已生成' if s['pdf'] else '未生成（仅 SVG）'}；交付清单完整：{s['complete']}", ""]
    L += [f"- `{k}` sha256 {v}…" for k, v in sorted(s["artifacts"].items())]
    if s["in_step"]:
        L += ["", "**加工级 STEP 包含**：" + "；".join(s["in_step"]), "", "**不包含（交工厂 CAM）**：" + "；".join(s["not_in_step"])]
    failed = [st for st in s["steps"] if not st.get("ok")]
    if failed:
        L += ["", "## 失败的步骤", ""] + [f"- {st['step']}：{st.get('error', '')}" for st in failed]
    L += ["", "## 边界", "",
          "- GLB 为 L0 视觉预览；加工级 STEP 是简化几何，不含完整造型曲面。",
          "- 参考 NC 为注释轨迹，不可上机；仿真为采样去料。",
          "- 未做强度、疲劳校核，未经制造审核：`not_released`。"]
    return "\n".join(L) + "\n"


def write(run) -> Path:
    run = Path(run)
    s = summarize(run)
    (run / "report_summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1))
    (run / "REPORT.md").write_text(markdown(s))
    return run / "REPORT.md"


def main():
    ap = argparse.ArgumentParser(description="写出一次全链路运行的交付报告")
    ap.add_argument("run", type=Path)
    print(write(ap.parse_args().run))


if __name__ == "__main__":
    main()
