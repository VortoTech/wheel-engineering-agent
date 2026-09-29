# 轮毂智图 · Wheel Engineering Skill

**VortoTech · 从一张效果图或一句话，到可审核的锻造轮毂工程初稿：三维模型、加工级 STEP、工程图和 CNC 加工准备包。**

锻造轮毂工厂接单时，手里通常只有客户的效果图和确认单上的几项尺寸。照片看得出造型，看不出背面厚度、偏距和螺栓座，工程师要在 CAD 里从头重建。
本项目把这一步交给 **Agent + Wheel Engineering Skill**：**模型提议，代码裁决**。大模型只负责理解和造型建议，建模、校验和准备等级都由确定性代码决定，每个尺寸都记录来源、误差和是否校验过。

```text
订单图 / 一句话 + 确认单尺寸
   → 理解（已知 · 看到 · 假设 · 未知）→ 配方 → 三维模型
   → 加工级 STEP → 工程图 → 工序单 · 刀具表 · 参考 NC → 采样去料仿真
   → 交付报告 REPORT.md（not_released，交工程师审核）
```

| 想做什么 | 看这里 |
| --- | --- |
| 在自己电脑上复现（无需 GPU、无需私有数据） | [REPRODUCE.md](REPRODUCE.md) |
| 让 Claude Code / Codex 用 Skill 走完全链路 | [Agent 使用 Skill 走完全链路](docs/skill-walkthrough.md) |
| Skill 本身 | [skills/wheel-engineering/SKILL.md](skills/wheel-engineering/SKILL.md) |
| 产品架构、PRD 与路线图 | [产品全貌](docs/product-architecture-roadmap.md) · [架构说明](docs/architecture.md) |
| 在本机或 DGX Spark 上选择、部署并验收本地模型 | 姊妹项目 [LocalPilot](https://github.com/DingHappy/localpilot) |

## 效果

![轮毂工程工作台：参考图、确认尺寸、三维预览与造型对照](docs/assets/readme/workbench.jpg)

*真实工作台截图（模型在 DGX Spark，CAD 在 Mac）：左侧输入与工程尺寸及其来源，中间三维预览，右侧造型 Agent、校验、交付与对照。界面旧名 WheelCAM。*

| 造型 Agent 修正前（预设） | 修正后 |
| :---: | :---: |
| ![初始造型](docs/assets/readme/style-before.png) | ![修正后造型](docs/assets/readme/style-after.png) |
| 外圈盲窗 0 个；窗口斜面 16/22 mm | 外圈盲窗 15 个；斜面 4/6 mm；工程尺寸不变 |

| 工程图审阅 | 加工级 STEP 与采样去料 |
| :---: | :---: |
| ![工程图](docs/assets/readme/drawing-viewer.jpg) | ![毛坯、粗加工结果、目标零件](docs/assets/readme/machining-simulation.png) |
| 正视图、A–A 剖面、孔系与技术要求；未发布章 | 毛坯 → 窗口粗加工 → 目标零件；1.5 mm 栅格下无新增过切 |

## 结果

**13 个真实锻造订单**（工厂 CAD 只做对照，不做输入；2026-09-28，DGX Spark）：

| 指标 | 结果 |
| --- | --- |
| 加工级 STEP（回转体 + 直壁通窗 + 锥座孔系） | **13/13** 单实体、STEP 回读、无碎面 |
| 外径 / ET / PCD · 孔数 · 中心孔 | 误差 **0** / **±0.05 mm** / 准确 |
| 螺栓孔径（确认单给出孔型的 9 个） | 误差 **0** |
| 总宽 | 同批标定平均误差 1.76 mm；**留一法（新订单预期）平均 2.03 mm**，最大 5.38 mm |
| 轮辋截面 | 中位误差 1.98 mm（0.94–3.03 mm），同批残差 |
| 完整造型 STEP | 13/13 写出，5/13 通过回读，1/13 全部检查通过：这是当前最大短板 |

**Skill 的作用**（同一个 Step3-VL-10B 模型，两款 × 完整 / 缺 ET 四个场景）：通用 CSG 工具 **0/4** 得到有效 STEP，Wheel Skill **4/4**。
不按 Skill 规则调用同一套工具时，螺栓孔用了模板的 22 mm（订单是 14 mm），检查却全部“通过”；按 Skill 调用，孔型来自确认单，六项规格都带来源。见 [对照结果](docs/skill-comparison-results.md) 与 [走查](docs/skill-walkthrough.md)。

**端到端**：Spark 上 M59 全链 5/5 步、57.7 s；公开示例订单在全新环境中 4/4 步，Spark 26 s、Mac 53 s，内存峰值约 1.1 GB，不需要 GPU。

轮缘模板由同一批 13 个 CAD 标定（[标定记录](docs/real-orders-template-calibration.md)）；指标定义与限制见 [最终 Demo PRD](docs/prd-final-demo.md)。

## 快速开始

需要 Python 3.12、Git，macOS 或 Linux；Linux 另装 `libgl1 libglib2.0-0 libxrender1 libsm6`。完整说明、pip 安装方式与实测耗时见 [REPRODUCE.md](REPRODUCE.md)。

```bash
git clone https://github.com/VortoTech/wheel-engineering-agent.git
cd wheel-engineering-agent
uv sync --locked --extra test
export WHEEL_ENGINEERING_RUNTIME="$PWD"
python3 skills/wheel-engineering/scripts/run.py doctor
```

**1. 公开示例订单走全链路（不需要模型）**

```bash
python3 skills/wheel-engineering/scripts/run.py chain examples/sample-order --out runs/sample
cat runs/sample/REPORT.md          # 规格来源、待确认项、每项检查、交付物、边界
```

**2. 用自己的图片**：已知规格写在 `--spec`，来源写在 `--spec-evidence`，孔型写在 `--hole-form`。

```bash
python3 skills/wheel-engineering/scripts/run.py chain --front front.jpg \
  --spec '{"diameter_in":20,"width_in":10.5,"et_mm":15,"pcd_mm":112,"bolts":5,"center_bore_mm":66.6}' \
  --spec-evidence '{"pcd_mm":{"source":"drawing","reference":"确认单"}}' --hole-form 15X32X60 --out runs/my-wheel
```

**3. 用一句话（需要模型）**

```bash
export WHEELCAM_CHAT_BASE_URL=http://127.0.0.1:8000/v1 WHEELCAM_CHAT_MODEL=<模型名>
python3 skills/wheel-engineering/scripts/run.py chain --text "做一个 20 寸 5 辐直辐轮毂，20×9 ET35，5×114.3，中心孔 73.1。" --out runs/text
```

**4. 交给 Agent**：在仓库里打开 Claude Code 或 Codex，说“用 wheel-engineering Skill 把 examples/sample-order 走完全链路，给我交付报告”。

**5. 工作台（可选界面）**：`PYTHONPATH=services .venv/bin/python -m wheelcam.workbench --runs runs/demo --port 8795`，打开 <http://127.0.0.1:8795>。

每次使用新的或空的输出目录。所有产物保持 `manufacturing_status=not_released`。

## 工作原理

### Skill：把专业规则交给 Agent

```text
Understand        Reason              Reconstruct         Verify               Report
理解输入     →    区分已知/看到/     →  配方 → 网格 /    →  单实体 · 尺寸 ·   →  REPORT.md：来源、
（图片或文字）    假设/未知，锁定尺寸    加工级 STEP          ET · 孔系 · 回读      待确认、检查、边界
```

`SKILL.md` 规定 Agent 必须做什么：规格要带来源；不从照片读取毫米尺寸；缺少决定加工的尺寸时先问用户；逐份报告核实；按 `REPORT.md` 汇报，不添加报告以外的数字。
可执行部分在 `services/wheelcam/`：Agent（经 `skills/wheel-engineering/scripts/run.py`）、命令行（`scripts/demo_chain.py`）和工作台调用同一套代码和校验。

没有模型时，图片 + 规格仍能走完全链路并执行全部校验；只有文字新建、对话修改和造型 Agent 需要模型。

### 模型：只提议，不裁决

| 用途 | 默认模型 | 说明 |
| --- | --- | --- |
| 文字 → 配方、对话 → 白名单参数、看图答“有没有” | 阶跃星辰 **Step3-VL-10B-FP8**，vLLM 本地部署（DGX Spark） | 订单图不出机；工程尺寸由确认单锁定，模型改不了 |
| 可替换 | `step-3.7-flash`、`step-5-preview`（阶跃星辰 API），任意 OpenAI 兼容服务 | 已实测全链路可用；云端会收到订单内容，真实订单只发给授权服务 |

造型 Agent 把工作分给擅长的工具：视觉模型只回答“外圈有没有盲窗”这类是非题；数量从照片的周期里数；斜面宽度靠渲染候选、比对照片边缘来选；每项修改都走白名单并重建校验。
三维几何全部由 manifold3d（网格）和 OpenCascade（STEP）生成，不用三维生成模型；PartPacker 等试过，未达到轮毂装配要求。
模型选择、配置需求与 Step-3.7-Flash 的本地部署见 [REPRODUCE.md](REPRODUCE.md#用哪个模型需要什么配置)。

### 本地模型从哪来：LocalPilot

[LocalPilot](https://github.com/DingHappy/localpilot) 是同一作者的姊妹项目：**“AI that configures AI”**。说出想在本地跑的模型，它负责选择推理引擎（vLLM、SGLang、llama.cpp 等）、精度（NVFP4 / FP8 / BF16）和服务配置，在你的机器上实测对比候选，并记住最优配置；面向 Apple Silicon、DGX Spark 和其他 CUDA 设备，也带有供 Agent 使用的 Skill。

两者分工：**LocalPilot 把本地模型服务配好并验收，Wheel Engineering 用这个服务做轮毂工程。** 仓库里的 [LocalPilot 接入技能](skills/localpilot/SKILL.md) 负责交接：检查节点前置条件，从 LocalPilot 取得实际的引擎端点、模型名和验收记录，再交给 Wheel Engineering。已有可用的模型服务时可以跳过 LocalPilot。
Wheel Engineering 直接连接推理引擎的端点，不经过 LocalPilot 网关：实测该网关会合并消息内容、丢弃助手轮次，尚未验证可以传递图片或多轮对话。

### 产物

| 产物 | 用途与边界 |
| --- | --- |
| `recipe.json` | 参数化配方，可修改后重建 |
| GLB | 造型预览，准备等级 L0 |
| `stock.step`、`machining.step` | 加工级毛坯与零件：回转体、直壁通窗、锥座孔系；不含辐条曲面、窗口斜面、盲窗 |
| 完整造型 STEP 候选 | `photo --kernel brep`；复杂造型可能失败或跳过工序，以报告为准 |
| 工程图、工序单、刀具表、参考 NC、仿真图 | 加工准备草案；NC 为注释轨迹，不可上机 |
| `REPORT.md`、`delivery_manifest.json` | 交付报告与全部产物的 SHA-256 |

## DGX Spark

| 层级 | 环境（2026-09-28 只读核对，[清单](docs/evidence/spark-environment-20260928.json)） |
| --- | --- |
| 硬件与系统 | NVIDIA GB10，ARM64，约 119 GiB 统一内存；Ubuntu 24.04.3，驱动 580.82.09 |
| 模型服务 | `nvcr.io/nvidia/vllm:26.02-py3`，vLLM 0.15.1，`step3-vl-10b-fp8`，上下文 8192 |
| CAD 应用 | Python 3.12 + CadQuery/OCCT + manifold3d（[Dockerfile.spark](Dockerfile.spark)），6 CPU / 16 GiB 限制下完成整链 |

GPU 用于模型推理；B-Rep 与 STEP 由 CPU 构建，不声称 CAD 获得 GPU 加速。两种运行方式：本机工作台 + Spark 模型（`bash scripts/start_workbench_spark.sh runs/demo 8795 18096`，CAD 在本机）；或在 Spark 上运行 `scripts/demo_chain.py`，模型与 CAD 都在 Spark（[全链证据](docs/spark-full-chain-evidence.md)）。模型只绑定回环地址，经 SSH 隧道访问。

Spark 上的模型服务可以用 [LocalPilot](https://github.com/DingHappy/localpilot) 选择配置和验收，见上文“本地模型从哪来”。

## 局限

- 完整造型 STEP 成功率低（5/13 回读），加工级 STEP 不含造型曲面，精加工交工厂 CAM。
- 误差数字来自参与模板标定的同一批 CAD；只有总宽做了留一法。
- 仿真是 2.5 维采样去料，不含刀柄、夹具和机床碰撞；未做强度与疲劳校核；未经制造审核。
- 旋压方向辐、银色与双色照片、扭转辐尚未支持。

## 仓库结构

| 目录 | 内容 |
| --- | --- |
| `skills/wheel-engineering/` | 可单独安装的 Skill：`SKILL.md`、`scripts/run.py`、`references/` |
| `services/wheelcam/` | Skill 实现、几何内核封装、校验、报告与工作台 |
| `scripts/` | 全链编排 `demo_chain.py`、示例订单生成、启动与评测工具 |
| `examples/sample-order/` | 公开示例订单（由参数预设生成，不含客户数据） |
| `tests/` | 行为、工程约束与隐私测试 |
| `docs/` | 架构、PRD、实测证据与记录 |
| `experiments/`、`demo-video/`、`apps/web/` | 研究实验、演示视频源码、较早的界面 |

`.agents/skills/` 与 `.claude/skills/` 链接到同一 Skill 目录；维护时只改 `skills/wheel-engineering/`。Python 包名 `wheelcam` 与 `WHEELCAM_*` 环境变量为兼容保留。
依赖由 `uv.lock` 锁定，`requirements-runtime.txt` 带哈希供 pip 与容器使用；[Core regression](.github/workflows/core-regression.yml) 在每次提交时检查安装与核心测试。

## 许可与数据

由 **VortoTech 团队**维护，代码与文档以 [Apache License 2.0](LICENSE) 开源。README 截图（`docs/assets/readme/`，含真实订单图）与 Vossen 基准配方不在许可范围内，仅供查看与复现，详见 [NOTICE](NOTICE)。
真实订单照片、工厂 CAD、私有造型库、模型权重和密钥不随源码分发（`data/`、`runs/`、`artifacts/` 与 `.env` 均被忽略）。隐私检查见 [隐私与发布检查](docs/privacy-release.md)。
