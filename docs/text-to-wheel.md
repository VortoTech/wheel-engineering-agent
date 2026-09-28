# 需求：一句话生成轮毂（文字 → 轮毂）

2026-09-28 起草，给 Codex 实现。比赛 2026-09-29 结束，控制在 2–3 小时。

## 0. 开工前先做

把尚未提交的文件提交（全链路依赖它们，仓库里现在跑不通）：
`scripts/build_manufacturing_step_demo.py`、`services/wheelcam/manufacturing_step_demo.py`、
`scripts/build_manufacturing_demo.py`、`services/wheelcam/manufacturing_demo.py` 及其测试，`.agents/`（Skill 定义），
`services/wheelcam/drawing.py`、`forged_photo.py` 的修改，`docs/` 下的几份文档。
**不要提交 `.env.bak-before-stepfun`。** 提交前跑 `tests/test_privacy.py`。

## 1. 目标

用户用自然语言描述一个轮毂，系统生成可继续走全链路的配方和模型：

> 20×10.5 ET15，5×112，中心孔 66.6，孔型 15X32X60。做 5 辐直辐，辐条宽一点，凹深一点，外圈加一圈盲窗。

输出与照片重建相同：`recipe.json`、`wheel.glb`、`engineering_report.json`，之后可以接加工级 STEP、工程图、加工包和对话修改。

## 2. 流程

1. **理解**（Spark 本地模型）：文字 → JSON 提案，结构见第 3 节。
2. **审核**（确定性代码）：白名单 + 范围，超范围裁剪并记录，不认识的字段拒绝。工程尺寸只能来自用户原文，**模型不得编造**；没说的规格用默认值，来源标为 `default`，并写进 `questions`。
3. **建模**：选辐条类型和预设 → 填参数 → 参数化辐条转成窗口轮廓 → 网格内核建模 → `mesh_build.verify` 校验 → 正视图渲染（`front_render.render_front`）。
4. **报告**：每个参数的来源（`user` / `model_choice` / `default`）、被拒绝和被裁剪的项、校验结果；`manufacturing_status=not_released`，等级 L0（网格）。

## 3. 模型输出的 JSON（建议）

```json
{
  "spec": {"diameter_in": 20, "width_in": 10.5, "et_mm": 15, "pcd_mm": 112, "bolts": 5, "center_bore_mm": 66.6},
  "hole_form": "15X32X60",
  "family": "single | y_split | skeleton",
  "preset": "work6-tapered | wide6-centre-groove | hf6-y-split | tree6-branching | v12-hub-fork",
  "spokes": 5,
  "shape": {"stem_w_hub": 46, "stem_w_split": 34, "split_r": 132, "arm_angle_deg": 13.5, "arm_w": 22},
  "style": {"hub_z": -95, "flank_w": 4, "flank_depth": 6, "lip_pockets": 15},
  "reply": "一句中文说明",
  "unknown": ["用户没说的项"]
}
```

- `spec` 只填用户原文里出现的数；审核时逐项对照原文（数值必须能在原文里找到），找不到就丢弃并改用默认值。
- `style` 直接复用 `recipe_chat.STYLE` 的白名单和范围，用 `recipe_chat.review` 做审核。
- `shape` 需要新增一份辐条形态的白名单和范围（参考 `forged_blank.ForgedWheel` 的 `stem_w_hub`、`stem_w_split`、`split_r`、`arm_angle_deg`、`arm_w`、`arm_bow`、`spoke_sweep_deg`），范围按预设的取值上下各放宽一截即可。
- `spokes`：3–12。

## 4. 已验证可行的部分（2026-09-28）

参数化辐条转轮廓后用网格内核建模：`experiments/forged-blank/recipes/` 下 5 个预设中，4 个 2–13 s 建成且校验全通过；
`v12-hub-fork` 用了 277 s，且有一项校验未过，需要查原因，或先不开放这个预设。转换代码（`tests/test_mesh_build.py` 的 `outline_recipe` 也是这个做法）：

```python
import numpy as np
import wheelcam.forged_blank as fb

def to_outline(d):
    """Parametric-family recipe dict -> outline-family recipe dict (group-0 windows), for mesh_build."""
    p = fb.recipe_from_dict(d)
    loops = [np.array(o) for o in fb.window_outlines(p, samples=96)]
    pitch = 360 / p.spokes
    g0 = [o for o in loops if -pitch / 2 <= np.degrees(np.arctan2(*o.mean(0)[::-1])) < pitch / 2]
    return {**d, "family": "outline",
            "outlines": [[[float(np.hypot(x, y)), float(np.degrees(np.arctan2(y, x)))] for x, y in o] for o in g0]}
```

规格 → 轮辋尺寸用 `wheel_skill.envelope_from_specs`（已按 13 个 CAD 标定），孔型用 `forged_blank.hole_form`，ET 的保持方式参考 `wheel_skill` 里 `web_thick_hub` 的反推。

## 5. 模型调用

- 端点：Spark 上已在运行的 `lp-vllm`（`127.0.0.1:8000`，模型 `step3-vl-10b-fp8`，只调用、不重启、不改配置），本机经 SSH 隧道。
- 环境变量沿用 `WHEELCAM_CHAT_BASE_URL` / `WHEELCAM_CHAT_MODEL`。
- 文字任务可照 `recipe_chat.ask_model`：`response_format=json_object`，解析失败追问一次，仍失败就明确报错，不猜。实测每轮约 5–10 s。
- 这个模型总会先思考；如果输出超长或截断，可以用 `style_agent.ask_vlm` 的做法：在 assistant 消息里预填 `</think>\n{`，并设 `continue_final_message=true`、`add_generation_prompt=false`。

## 6. 文件与分工

- 新建：`services/wheelcam/text_wheel.py`（CLI：`python -m wheelcam.text_wheel "描述" --out DIR`）、`tests/test_text_wheel.py`。
- 接入：`scripts/demo_chain.py` 增加文字入口（例如 `--text "描述"`，替代 case 目录；此时没有工厂 CAD，跳过 compare 和原图对照）；工作台 `services/wheelcam/workbench.py/.html` 增加"用文字新建"入口。**这两处在 Codex 做这项功能期间归 Codex 改，Claude 不动。**
- 不要改：`forged_blank.py`、`mesh_build.py`、`machining_step.py`、`recipe_chat.py`、`style_agent.py`、`front_render.py`（需要改就先说）。

## 7. 验收

1. 上面那句示例生成 5 辐直辐、外圈 15 个盲窗，规格全部来自原文（来源 `user`），校验全部通过，10 s 内建完。
2. "做一个 6 辐 Y 形分叉的 21 寸轮毂"：没给的 PCD、ET 等用默认值，来源 `default`，并在 `questions` 里逐项追问。
3. "像某品牌某款那样" / "扭转辐"：模板做不到的，明确说明，不乱生成。
4. 原文里有"PCD 114.3"时，模型输出别的 PCD 必须被审核改回原文值或拒绝。
5. 生成结果能直接接 `machining_step.export` 出加工级 STEP，并通过其校验。
6. 测试不依赖模型服务（用假函数替代调用），并跑 `tests/test_privacy.py`。
