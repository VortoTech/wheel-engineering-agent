# Wheel Engineering Agent

**VortoTech · Engineering Reconstruction Agent 的轮毂垂直实现**

把轮毂图片或文字需求、已知工程尺寸与专业规则转化为可调整、可追溯、可验证的 CAD **草稿**。

核心能力是 **[Wheel Engineering Skill](SKILL.md)**：理解结构 → 区分已知与未知 → 生成配方 → 重建 → 校验 → 报告。工作台允许工程师持续对话修改、查看渲染和重新生成交付包。

> 当前为工程辅助原型。GLB 是 L0 视觉预览；加工级 STEP 使用简化几何，不包含全部造型曲面。工程图、参考 NC 和采样材料去除仿真均未经过制造批准，输出保持 `not_released`。

## Skill 在项目中做什么

| 阶段 | Skill 的职责 | 实现入口 |
| --- | --- | --- |
| Understand | 接收图片、文字和规格；形成结构与参数候选 | `wheel_skill.py`、`text_wheel.py` |
| Reason | 保存来源，区分已知、假设、未知；锁定工程尺寸 | `wheel_skill_contract.py`、`wheel_skill.py` |
| Reconstruct | 生成可重建配方；按选择构建网格或 B-Rep | `wheel_skill.py`、`mesh_build.py`、`forged_blank.py` |
| Refine | 可选视觉 Agent 修正造型；对白名单内参数做对话修改 | `style_agent.py`、`recipe_chat.py` |
| Verify | 校验几何、规格、STEP 回读；保留跳过工序与失败 | `wheel_skill.py`、下游几何模块 |
| Report | 记录参数来源、未知项、检查、准备等级和交付状态 | `engineering_report.json`、各产物报告 |

**Skill 有两个层面：**

- 根目录 [`SKILL.md`](SKILL.md) 是给开发助手调用和评审能力的使用契约。
- `services/wheelcam/` 中的 Python 模块是实际执行的技能。工作台调用这些代码，不读取 Markdown 来运行建模。

`.agents/skills/wheel-engineering/` 与 `.claude/skills/wheel-engineering/` 仅提供工具发现入口，`SKILL.md` 都链接到根目录同一文件。当前仓库没有 `.codex/skills`。保留两个入口是为了兼容不同开发助手，不表示运行了两个 Agent，也不表示工作台依赖 Codex 或 Claude。

## 当前运行架构

```text
工作台 workbench.py + workbench.html / CLI demo_chain.py
  ├─ 图片 + 规格 → wheel_skill.py
  ├─ 文字需求   → text_wheel.py
  └─ 对话修改   → recipe_chat.py / 工程确认 workbench_revision.py
                         ↓
          参数契约 + 来源 + UNKNOWN + 配方
                         ↓
       网格预览 / B-Rep 候选 → 校验与报告
                         ↓
       选定配方快照 → machining_step.py
                         ↓
        stock.step + machining.step
                         ↓
       加工准备包 / 采样仿真 / 工程图 / 交付清单
```

LLM/VLM 提出候选；确定性代码约束参数、构建和检查。模型无法凭照片认证尺寸或制造可行性。LocalPilot 是此前讨论的运行平台方向，当前代码不应被描述为已集成通用 LocalPilot Runtime。

详见 [架构与目录导航](docs/architecture.md)。

## 启动当前演示工作台

需要 Python 3.12 或 3.13、`uv`。从仓库根目录执行：

```bash
uv sync --extra test
PYTHONPATH=services .venv/bin/python -m wheelcam.workbench --runs runs/demo --port 8795
```

打开 <http://127.0.0.1:8795>。首次运行没有已有案例，使用“用图片新建”或“用文字新建”。文字生成与对话需要配置模型；未配置时不会自动获得模型能力。

```bash
# 使用自己的 OpenAI-compatible 模型端点；不要把密钥写入 Git
export WHEELCAM_CHAT_BASE_URL=http://127.0.0.1:8000/v1
export WHEELCAM_CHAT_MODEL=your-model-id
export WHEELCAM_VLM_BASE_URL=http://127.0.0.1:8000/v1
export WHEELCAM_VLM_MODEL=your-vision-model-id
```

若已配置 SSH 别名 `spark`，远端已有兼容模型服务监听 8000 端口：

```bash
bash scripts/start_workbench_spark.sh runs/demo 8795 18096
```

这个启动器只建立模型隧道：**模型在 Spark，CAD 在本机**。Spark 全链运行另见 [全链运行与证据](docs/spark-full-chain-evidence.md)；不可混为同一种验收。

## 输入与输出

- **图片路径**：照片 + 已知规格；缺失尺寸保持未知或明确假设。
- **文字路径**：支持的轮毂模板 + 明确尺寸；不保证任意自由形状生成。
- **持续修改**：对白名单内的造型参数修改、重建与检查；工程尺寸单独确认。
- **交付路径**：从选定配方重新生成加工 STEP、工程图、加工准备包和验证记录。预览修改后，旧交付包不能代表新版本。

输出保存在 `runs/` 或 `artifacts/`，不随源码分发。STEP 是几何交换格式，不携带原生 CAD 特征历史；配方用于参数化重建。详细操作见 [SKILL.md](SKILL.md)、[文字输入](docs/text-to-wheel.md)、[最终 Demo PRD](docs/prd-final-demo.md)。

## 目录与维护边界

| 目录 | 用途 |
| --- | --- |
| `SKILL.md` | 唯一维护的 Skill 使用契约 |
| `services/wheelcam/` | 当前工作台、技能、几何、验证与交付模块 |
| `scripts/` | 全链编排、启动、评测与工具脚本 |
| `tests/` | 功能、工程边界与隐私检查 |
| `docs/` | 产品、架构、部署、评测证据与历史设计记录 |
| `demo-video/` | 演示视频源码；运行素材不入库 |
| `experiments/` | 研究与评测工具，不是默认生产链路 |
| `apps/web/` | 较早的 Web 界面；当前参赛工作台使用 `workbench.html` |
| `data/`、`runs/`、`artifacts/` | 本地输入和产物，Git 忽略 |

Python 包名 `wheelcam` 和环境变量 `WHEELCAM_*` 保留兼容现有部署；产品名称不要求同步更改内部导入路径。

## 证据与限制

- [Spark 全链及工作台复测](docs/workbench-hardening.md)：明确执行设备与产物版本。
- [有／无 Skill 对照](docs/skill-comparison-results.md)：只适用于记录的模型和协议，不能泛化成所有通用模型的结论。
- [评测契约](docs/skill-evaluation.md)：冻结配置、记录失败、分离视觉与工程指标。
- 13 个真实订单参与过模板标定，不能当作独立泛化测试集。
- 视觉同图边缘分数不等于工程尺寸精度；采样无过切不等于完整加工仿真通过。
- 当前没有结构强度、疲劳或制造审核批准。

## 团队与数据安全

团队仓库归 VortoTech 管理，默认保持私有。源码不包含运行所需的私人照片、工厂 CAD、模型权重或配置密钥。复现实验需要团队按授权单独准备数据。

```bash
.venv/bin/python -m pytest tests/test_privacy.py -q
```

`.env`、`.env.*`（模板除外）、运行产物与缓存被忽略。路径脱敏只清理当前文件，不会自动删除旧 Git 历史。公开仓库前仍需复查历史、附件及数据使用授权。见 [隐私与发布检查](docs/privacy-release.md)。
