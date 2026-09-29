# Agent 使用 Wheel Engineering Skill 走完全链路（2026-09-29 实测）

Agent 通过 Skill（`skills/wheel-engineering/`）从文字或图片出发，一条命令完成：三维模型 → 加工级 STEP → 工程图 → 加工包 → 交付报告。
工作台只是另一种界面，调用的是同一套代码和校验。

以下由 Claude Code 加载 `wheel-engineering` Skill 后执行，全部使用仓库里的公开示例订单 `examples/sample-order/`，在 Mac（arm64）本机建模。

## 1. 流程

`SKILL.md` 的“End to end”一节规定 Agent 按以下顺序执行：

1. `doctor` 检查运行环境。
2. 区分输入：图片 + 规格，或文字；记下每个值的来源。不从照片读取毫米尺寸。
3. 先理解再建模：`photo ... --no-build` 或 `text ...`，读取追问、未知项和证据分组。
4. 判断是否要先问用户：六项关键规格和孔型决定加工级 STEP 与工程图，缺了就先问；造型问题（盲窗、斜面、凹深）不阻断交付，写进报告。
5. 跑全链路：`chain --front ... --spec ... --spec-evidence ... --hole-form ...` 或 `chain --text '...'`。
6. 核实：链路自动写出 `REPORT.md` 和 `report_summary.json`，内容全部来自本次运行的文件；有失败步骤就不能声称交付完成。
7. 向用户汇报：转述 `REPORT.md`，写明执行设备和模型，不添加报告以外的数字，以待确认问题结尾。

## 2. 端到端实测

| 输入 | 命令（`run.py` 为 `skills/wheel-engineering/scripts/run.py`） | 结果 |
|---|---|---|
| 图片 + 规格 | `run.py chain --front examples/sample-order/front.jpg --spec '{…6 项…}' --spec-evidence '{…确认单…}' --hole-form 14X28X60 --out runs/e2e-photo` | 4/4 步，40 s；加工级 STEP 四项检查通过；PDF 已生成；5 项待确认 |
| 文字（模型：Spark 上的 Step3-VL-10B，经 SSH 隧道） | `run.py chain --text "做一个 20 寸 6 辐直辐轮毂，20×9 ET35，5×114.3，中心孔 73.1，孔型 14X28X60。" --out runs/e2e-text` | 4/4 步，40 s；六项规格和孔型均标为“用户提供”；3 项待确认 |

图片这一趟生成的 `REPORT.md` 开头：

```text
结论：链路 全部通过（40.1 s）；准备等级 L0；制造状态 not_released；待确认 5 项。所有产物都是待工程师审核的草案。
| PCD mm | 114.3 | 图纸/确认单（示例确认单） |
| 孔型   | 14X28X60 | 确认单/用户 |
待确认：没有斜视图，凹面深度用模板默认 …；锥座深度由模板 22 mm 上提到 14.9 mm，须工程师确认 …
加工级 STEP 不包含：辐条正面曲面、脊线、槽和窗口斜面；中心凹谷；外圈盲槽 …
```

## 3. 有 Skill 和没有 Skill 的区别

**同一个工具，不按 Skill 规则调用**：给同样的照片和规格，直接调用建模命令，不传来源、不传孔型。

| | 不按 Skill | 按 Skill |
|---|---|---|
| 螺栓孔 | 22 mm 直孔（模板默认，与订单不符） | 14 mm 孔 + 28 mm × 60° 锥座（确认单） |
| 六项规格来源 | 全部“未说明” | 全部“确认单” |
| 以后能否升到 L2 | 不能（来源不明确的尺寸不能提升等级） | 能（还需 STEP 回读和检查通过） |
| 检查结果 | **全部通过** | 全部通过 |

孔径错了 8 mm，检查却全部通过：检查只核对 PCD 和孔数，孔型是否来自确认单要由调用方传入。Skill 规定了哪些信息必须传、哪些不能从照片猜、什么时候必须先问用户、最后怎样核实与汇报。

**不给 Skill，也不给专用工具**：同一个 Step3-VL-10B 配通用 CSG 工具，在 Spark 上 4 次尝试 0 次得到有效 STEP（3 次输出截断，1 次不支持的操作）；Wheel Skill 4/4。见 [有／无 Skill 对照](skill-comparison-results.md)。

这次走查还发现并修正了一处报告措辞：没有斜视图时，追问曾写成“斜视图测得的凹面深度”，现在写为“模板默认的凹面深度（没有斜视图）”。

## 4. 评委复现

按 [REPRODUCE.md](../REPRODUCE.md) 安装后，在仓库根目录打开 Claude Code 或 Codex（两者都会发现 `.claude/skills/` 或 `.agents/skills/` 下的同一个 Skill），对它说：

> 用 wheel-engineering Skill，把 examples/sample-order 的正面图和 spec.json 里的规格（来源：示例确认单，孔型 14X28X60）走完全链路，最后给我交付报告。

预期：Agent 运行 `doctor` 和理解步骤，再调用 `chain`，最后按 `REPORT.md` 汇报 4/4 步通过、规格来源、待确认项和边界。
不用 Agent 也能执行同样的命令：

```bash
export WHEEL_ENGINEERING_RUNTIME=$PWD
python3 skills/wheel-engineering/scripts/run.py chain --front examples/sample-order/front.jpg \
  --spec "$(python3 -c "import json;print(json.dumps(json.load(open('examples/sample-order/spec.json'))['spec']))")" \
  --hole-form 14X28X60 --out runs/e2e-photo
cat runs/e2e-photo/REPORT.md
```

不传 `--spec-evidence` 时，报告会把六项规格的来源标为“来源未说明”，这正是上面对照中的情形。
