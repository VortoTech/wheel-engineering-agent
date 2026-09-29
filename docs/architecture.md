# 当前架构与目录导航

本页描述当前实现，不把平台规划当作已实现能力。根目录 README 是产品入口，SKILL.md 是技能调用契约，本页是开发维护入口。历史版本记录继续保留在原文档中。

## 1. 产品入口

`services/wheelcam/workbench.py` 提供 FastAPI API 并加载 `workbench.html`。当前参赛工作台使用这一对文件；`apps/web/` 和 `app.py` 属于较早界面与接口，不能混用它们的启动说明。

工作台负责输入、任务执行、版本选择、模型预览、对话、交付状态和证据展示。`scripts/demo_chain.py` 是批处理和交付链入口。当前没有独立通用的 LocalPilot Runtime 包。

## 2. Wheel Engineering Skill

| 模块 | 责任 | 不能混淆的边界 |
| --- | --- | --- |
| `wheel_skill_contract.py` | 输入规格、来源与理解契约 | 输入值不自动成为测量真值 |
| `wheel_skill.py` | 照片重建、配方、校验与准备等级 | 默认网格路径不生成 STEP |
| `text_wheel.py` | 文字解析、候选审查、模板配方与预览 | 与图片路径共享部分参数规则，不是调用整个图片重建函数 |
| `style_agent.py` | 可选视觉判断、渲染搜索与造型修正 | 不能修改工程尺寸，不能提升准备等级 |
| `recipe_chat.py` | 对话修改白名单、重建与检查 | 工作台的日常造型对话路径 |
| `workbench_revision.py` | 确认工程变更、配方快照与版本交付 | 修改后的预览不继承旧交付验证 |
| `agent_cad.py` | 另一路类型化 CAD Action 协议 | 不是当前工作台全部对话的统一入口 |

`SKILL.md` 是给开发助手的说明；业务代码不会动态加载它。`.agents` 与 `.claude` 的 Skill 入口链接到同一文件，避免规则漂移。技能实现包含多个模块，“一个 Skill”不等于“全部业务代码塞进一个文件”。

## 3. 几何和下游产物

- `forged_blank.py`：轮毂工程配方、轮廓和完整造型 B-Rep 路径。复杂布尔可能失败或跳过工序，必须读取报告。
- `mesh_build.py`：快速造型网格与预览校验。
- `machining_step.py`：由配方生成加工用简化实体及毛坯，产出 `machining.step`、`stock.step` 和调整记录。
- `manufacturing_demo.py`：加工准备、参考 NC、材料去除采样。不能替代机床、刀柄和夹具碰撞验证。
- `drawing.py`：工程图与技术要求；PDF 转换器缺失时可能只有 SVG。

完整造型 B-Rep 与加工级 STEP 是两条不同输出路径。不能以简化 STEP 成功证明完整造型曲面已构建。

## 4. 数据与证据

输入 → 规格与来源 → `recipe.json` → 构建产物与报告 → 配方变更记录 → 新版本交付。

`engineering_report.json` 记录理解、未知、准备等级等；`machining_report.json` 记录简化加工实体检查和调整；`chain.json` 记录每步执行状态与时间；`delivery_manifest.json` 用于绑定交付版本。没有生成的文件不能因前一步成功就标记完成。

`data/`、`runs/`、`artifacts/` 均为本地数据区域，不能作为仓库必须包含的源码依赖。

## 5. 运行设备

1. 本机独立运行：几何和工作台在本机，模型取决于所配置端点。
2. 本机工作台 + Spark 模型：`start_workbench_spark.sh` 建立隧道；CAD 仍在本机。
3. Spark 全链：在 Spark 虚拟环境或应用容器执行 `demo_chain.py`。详见 [全链证据](spark-full-chain-evidence.md) 与 [后续复测](workbench-hardening.md)。

## 6. 本次整理及后续代码拆分顺序

本次统一文档与 Skill 单一来源，修正默认入口和目录职责，保留现有导入路径，避免破坏经过评测的几何链路。

后续可以依次拆分：

1. 从 `wheel_skill.py` 抽出参数来源和 readiness 规则，供文字、图片与对话路径共享。
2. 从 `workbench.py` 抽出任务执行与产物索引，界面路由仅处理输入输出。
3. 统一各路径的验证结果结构，并显式保留完整造型／简化加工实体差别。
4. 明确旧 `apps/web` 接口仍有无用户，再决定归档；不要直接删除研究代码和历史证据。

这些是后续重构计划，不声称已经实现。每次拆分都应保持现有案例的配方、检查与交付结果可对照。
