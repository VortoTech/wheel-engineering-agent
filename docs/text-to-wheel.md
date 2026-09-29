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

## 8. 第二轮：文字新建的造型质量（2026-09-28 录屏发现）

第一轮 6 条验收都已通过，但录制演示视频时，文字新建生成的轮毂明显比照片重建的粗糙。实测原因和要改的地方如下。
本轮 `text_wheel.py`、`workbench.html` 的文字新建部分仍归 Codex；第 6 节"不要改"的文件照旧。

### 8.1 现象与原因

| 现象 | 实测原因 |
|---|---|
| 轮毂像一块平板，没有斜面和凹深 | 模型给 `style`、`shape` 字段返回空字符串 `''`，或在数字字段填"Y形分叉"。审核全部拒绝（一次拒 7 个字段），斜面、凹深退回 0 |
| Y 形分叉处有凸耳，中间一大块平盘 | `hf6-y-split` 预设本身就是这个样子（把 4 个预设原样渲染过），不是模型改坏的。现有预设都是早期研究用的参数模板 |
| "外圈加一圈盲窗"没有生效 | `pocket_match` 只认带数量的写法（"外圈 15 个盲窗"）；模型返回的 `lip_pockets` 是 `''`，被拒绝 |
| 写"外圈 15 个盲窗"时，只出现 5 条长槽 | 文字路径下每个窗口外侧的几个盲窗连成一条弧形长槽，不是 M59 那样 15 个独立小窗 |
| 完成后顶部状态栏仍是"正在处理请求" | 工作台文字新建的完成回调没有更新 `#task-status` |
| 连续两次文字新建，描述被拼在一起 | 对话框打开时不清空 `#text-input` |

复现：`python -m wheelcam.text_wheel "做一个 21 寸、6 辐 Y 形分叉的轮毂，外圈加一圈盲窗，孔距 5×114.3。" --out 新目录`，看 `engineering_report.json` 的 `rejected` 和 `front.png`；再用"做一个 20 寸 5 辐直辐轮毂，辐条宽一点，外圈 15 个盲窗，孔距 5×112。"看长槽。

### 8.2 要做的事（按优先级）

1. **用真实订单的重建结果做造型库。** 照片重建出来的窗口轮廓（`family: outline`）比参数预设好看得多，M59 就是这样来的。
   - 来源：本机 `runs/real-orders-eval/mesh/<case>/build/recipe.json`，共 13 个。
   - 只取造型字段：`spokes`、`outlines`、`flank_w`、`flank_depth`、`spoke_pad_depth`、`hub_valley_depth`、`hub_arm_w`、`concavity_exp`、`lip_pockets`、`lip_pocket_r` 等。工程尺寸（外径、宽度、ET、PCD、孔数、中心孔、孔型）一律由文字原文或默认值决定，不从模板带过来；换外径时轮廓按比例缩放。
   - 每个模板给一个中性编号和简短描述（如"5 辐直辐 · 外圈盲窗"），供模型选择。不要带 case 号、尺寸组合或任何订单信息。
   - **隐私**：这些轮廓来自客户订单。造型库文件放在 `runs/` 下由脚本生成，**不提交 Git、不放进公开材料**；是否可以公开由用户决定，先问。仓库里只提交生成脚本和测试（测试用合成轮廓）。
   - 模型选模板的范围只限造型库和辐条数匹配的条目；没有匹配时再退回参数预设，并在报告里写明"参数模板，造型较简化"。
   - 注意：`services/wheelcam/case_library.py` 是旧界面的案例库，与此无关，不要混用。
2. **模型没给有效造型参数时，用验证过的默认值，不要退回 0。**
   - 默认值取 M59 经造型 Agent 修正后的一组：`flank_w 4`、`flank_depth 6`、`spoke_pad_depth 10`、`concavity_exp 1.0`、`hub_z -80`，来源标 `default`。
   - 选中造型库模板时，默认值就用模板自己的值。
   - 收紧提示词：数字字段只能是数字，不知道就省略该字段，不要输出空字符串；空字符串视为"未提供"，不计入 `rejected`。
   - 可参考 `style_agent.ask_vlm` 的预填做法，让模型直接输出 JSON。
3. **盲窗。**
   - 修掉连成长槽的问题：同样的 `lip_pockets` 在文字路径下应和 M59 一样是独立小窗。先对比 `runs/demo-live-20260928b/*/reconstruct/recipe.json`（M59，15 个独立小窗）和文字路径的配方，找出差异（`lip_pocket_r`、窗口轮廓数量等）。
   - 只说"一圈盲窗"、没给数量时，默认取辐条数的 3 倍，来源 `default`，并在 `questions` 里追问数量。
4. **工作台文字新建。** 打开对话框时清空输入；完成后更新顶部状态栏；新版本生成后自动切到它。
5. **先下线 `hf6-y-split`、`tree6-branching`、`v12-hub-fork` 三个预设**，或在界面上标"实验"。造型库里有对应款式后再开放。

### 8.3 验收

1. "做一个 20 寸 5 辐直辐轮毂，外圈 15 个盲窗，孔距 5×112。" 选中造型库里的 5 辐模板，正视图与 M59 照片重建版观感接近（直辐、窗口斜面、15 个独立盲窗），网格检查全部通过。
2. "做一个 20 寸 6 辐轮毂，外圈加一圈盲窗。" 生成 18 个独立盲窗，来源 `default`，`questions` 里追问盲窗数量和缺失尺寸。
3. 模型返回空字符串或文字时，造型参数取默认值，模型不输出的项不出现在 `rejected` 里；轮毂不是平板（`flank_w > 0`）。
4. 同一描述连续新建两次，第二次的"重建依据"只含第二次的描述；完成后状态栏显示已完成。
5. 造型库文件不在 `git ls-files` 里；`tests/test_privacy.py` 通过；测试不依赖模型服务和 `runs/` 数据（没有造型库时跳过或用合成模板）。
6. 在工作台里用第 1 条描述实跑一次，截正视图和透视图附在交付说明里，供重录演示视频第 9 段使用。
