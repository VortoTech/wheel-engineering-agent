# Engineering Reconstruction Agent V1

日期：2026-09-22。首个领域实现：WheelCAM。

## 产品定义

Engineering Reconstruction Agent 将照片、多视图、规格和人工测量转为可编辑、可追溯、可验证的参数化 CAD。它不是 Image-to-Mesh，也不承诺单图恢复不可观测的背面和制造尺寸。

```text
Evidence → Engineering Vision → Geometry Reasoning → CAD IR
       → CAD Agent Tools → CadQuery / OCCT → B-Rep / STEP
       → Render + Geometry + Dimension Validation → Correct / Ask
```

WheelCAM 是该系统的第一个受限领域。通用机械零件扩展必须建立在轮毂领域闭环通过之后。

## Agent 的位置

Agent 是工作流的主动执行者，不只是聊天入口。它可以：

- 从图片和标注建立观测候选；
- 创建或替换受控草图，例如单组轮辐窗口轮廓；
- 修改参数、受支持的草图和建模策略；特征依赖顺序仍由版本化 Feature Tree 控制；
- 调用构建、渲染、比较和验证工具；
- 根据验证失败继续修改；
- 对不可观测信息标记 `UNKNOWN` 并请求指定视角或测量。

Agent 不能：

- 注入或执行任意 Python/CAD 宏；
- 把推断值改写成实测值；
- 绕过参数范围、几何约束、revision 冲突或验证门；
- 将视觉相似、有效 B-Rep 或 STEP 导出成功解释成制造批准；
- 自动把草稿推进为 `Manufacturing Ready`。

原则是：**Agent 决定下一步画什么、改什么；CAD IR 规定它能表达什么；几何内核判定能否构造；验证层判定结果处于什么成熟度。**

## 三层 CAD IR

### 1. Evidence IR

保存图片、视角、标尺、工程图、测量和点云的来源摘要。所有观测都绑定证据引用，不能只保存最后一个数字。

### 2. Design Intent IR

```text
Wheel
├── Rim / Revolve
├── Hub / Extrude or Revolve
├── CenterBore / Cut
├── BoltMaster / Cut
├── BoltPattern / CircularPattern
├── SpokeMaster / Loft or WindowSketch
├── SpokePattern / CircularPattern
└── JunctionFillets / Fillet
```

每个参数至少包含 `requested_value`、`applied_value`、`unit`、`source`、`confidence`、`constraints` 和 `validation_status`。当前 `wheel-engineering-v1` 是这一层的起点，后续升级必须保持版本化兼容。

### 3. Agent Action IR

当前可执行协议为 `wheel-agent-cad-plan-v1`：

| 操作 | 作用 | 当前边界 |
| --- | --- | --- |
| `set_parameter` | 修改 WheelSpec 参数 | 进入 Pydantic 和几何约束检查 |
| `replace_sketch` | 替换参数化草图 | V1 只允许 `window_outlines_mm` |
| `mark_unknown` | 声明当前证据无法确定参数 | 保留概念构建值，但工程语义输出为空 |
| `request_measurement` | 生成明确的补充测量问题 | 有未回答问题时不能声称输入完整 |
| `request_tool` | 请求确定性本地工具 | 只允许主图分析、窗口拟合和最新构建比较 |

`request_tool` 不直接运行任意代码，也不会把密集轮廓坐标交给语言模型生成。它只把任务路由到已注册工具；
工具产生的候选仍需独立 Schema、revision 和人工批准。

计划绑定 `base_revision`。同一计划不能重复修改同一参数；关键尺寸、轮辐数量和草图替换必须明确批准。所有已应用计划写入 `agent_cad_runs` 审计记录。

API：

```text
POST /api/projects/{id}/agent-cad/preview
POST /api/projects/{id}/agent-cad/apply
```

`preview` 不修改项目；`apply` 只接受没有待批准关键动作的计划。应用后产生新 revision，原计划无法重复覆盖新草稿。

## 模型编排器与工作台

工作台的 Agent 页签通过 OpenAI-compatible `/chat/completions` 接口连接可替换的多模态模型：

```bash
export WHEELCAM_AGENT_BASE_URL=http://127.0.0.1:8000/v1
export WHEELCAM_AGENT_MODEL=your-vision-model
# 远程服务需要时再设置；不得写入项目文件或日志
export WHEELCAM_AGENT_API_KEY=...
```

如果凭据已经保存在 PinPawo 的权限受控 Model Profile 中，优先只引用 profile，不复制 Token：

```bash
export WHEELCAM_AGENT_PROFILE=pinpawo:primary
```

WheelCAM 可以在不改写原 profile 的前提下覆盖模型与输入模态。例如 Xiaomi Token Plan：

```bash
export WHEELCAM_AGENT_PROFILE=pinpawo:primary
export WHEELCAM_AGENT_MODEL=mimo-v2.6-pro
export WHEELCAM_AGENT_INPUT_MODALITIES=text,image
```

WheelCAM 只在服务进程内读取该 profile 的 endpoint、model、输入模态、JSON 模式和凭据；状态接口、日志与前端均不
返回 Token。若 profile 只声明 `text`，系统不会向它发送图片，而是提供视觉算法摘要、参数和证据引用。

还可用 `WHEELCAM_AGENT_TIMEOUT_SECONDS` 设置请求超时。未配置 provider 时，API 和 UI 明确显示 `not_connected`，
不会运行规则模拟器冒充 AI。Provider 只能返回 `wheel-agent-cad-plan-v1`，返回内容仍须经过 Schema、WheelSpec 和审批门。

主参考图在前端与后端均默认**不发送**给模型。用户必须在 Agent 页签逐次勾选同意；不勾选时只发送当前参数、来源、视觉算法摘要和
Schema。密钥仅由服务端环境读取，状态接口不会返回密钥。

## Agent 建模闭环

```text
1. Inspect evidence
2. Build/modify typed CAD plan
3. Preview constraints and approval requirements
4. Apply to a new draft revision
5. Build B-Rep
6. STEP round-trip and geometry checks
7. Render from evidence camera
8. Compare silhouettes/features
9. Modify, request measurement, or stop with UNKNOWN
```

Agent 的优化目标必须拆开记录：视觉轮廓误差、特征位置误差、参数约束、B-Rep 完整性和 STEP 交换稳定性不能混成一个“正确率”。

## 成熟度状态

| 等级 | 定义 | 允许输出 |
| --- | --- | --- |
| L1 Visual CAD | 外观候选 | GLB/STL、视觉报告 |
| L2 Parametric CAD | 可编辑 Design Intent | 参数、Feature Tree、草稿 STEP |
| L3 Engineering CAD | 关键尺寸确认并通过独立工程检查 | 工程 STEP、重构报告 |
| L4 Manufacturing Ready | 材料、公差、CAE、法规、CAM 和现场验证完成 | 经责任人批准的制造发布包 |

状态由验证证据决定，不由 Agent 自报。WheelCAM 当前仍处于 L2 构建阶段，制造状态固定为 `NOT RELEASED`。

## 实施顺序

1. P0：稳定 Evidence / Design Intent / Agent Action 三层 Schema。
2. P1：参数化引擎完成 60 案例 B-Rep 和 STEP 回读矩阵。
3. P2：CAD 生成 RGB、Depth、Normal、Mask、Edge、Camera 和参数真值。
4. P3：正视图加一个已知尺寸，恢复二维参数化草图。
5. P4：Agent 自动执行 Build → Render → Compare → Modify，并能请求测量。当前已完成模型提案、审批、应用和
   “应用并生成 CAD”；Render/Compare 自动回灌到下一轮计划尚未完成。
6. P5：多视图、三维参数、复杂 Design Intent 与独立工程验证。

模型提供商、视觉模型或 LLM 可以替换；CAD IR、约束、审计和验证门是产品核心。
