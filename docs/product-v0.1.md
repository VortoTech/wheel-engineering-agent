# Engineering Reconstruction Agent · V0.1 PRD

2026-09-27。黑客松垂直实现：**Wheel Engineering Skill**。目标用户是机械设计、CAD 和逆向工程人员；任务是把轮毂正面图、可选的一张斜视图和少量已知规格整理成可复核的设计初稿。第三张照片和任意多视角融合属于后续扩展。工程师负责确认输入、修改和批准。当前项目没有取代工程师或发布制造文件的证据。

## 产品定义

Engineering Reconstruction Agent 将不完整的视觉信息、工程尺寸和专业规则转成**可重新生成、可追溯、可验证的参数化 CAD 草稿**。核心资产是视觉感知与 CAD 内核之间的 Engineering Constraint Intelligence：领域对象、输入参数、约束、来源与未知项、重建规则、验证规则。DGX Spark 是可选的本地视觉/Agent 运行目标；工程约束和 CAD 验证必须不依赖某一个模型或硬件品牌。

V0.1 只做轮毂：**Understand → Reason → Reconstruct → Verify → Report**。

| 步骤 | 本版应交付 | 当前实现与边界 |
| --- | --- | --- |
| Understand | 正视图结构候选、斜视图辅助深度、输入文件摘要 | `wheel_skill.reconstruct` 有窗口跟踪和组数候选；输入 SHA-256 写入报告；不自动证明零件型号 |
| Reason | 已提供、照片观测、规则估计、模板默认、未知分组；询问缺失尺寸 | `wheel_skill_contract.understanding` 输出分组、约束关系和问题；规则目前是轮毂专用确定性逻辑，不是通用求解器 |
| Reconstruct | 版本化配方、GLB 预览；可选 B-Rep 与 STEP | 网格路线快但只属 L0；锻坯 B-Rep 路线可产 STEP，复杂轮款仍可能失败；描述性重建计划不是原生 CAD 特征历史 |
| Verify | 单实体、STEP 回读、关键尺寸、孔系、旋转与可选照片叠加检查 | 仅在 B-Rep 上按几何检查结果提升等级；`--visual-check` 单独输出外观指标和叠加图，不提升工程等级；尚无疲劳、法规、真实毛坯和 CAM 验证 |
| Report | 来源、未知项、问询、逐项检查、等级和未发布状态 | `engineering_report.json`；网格不能报告成参数化 STEP；所有结果为 `not_released` |

## 六项 Skill 资产

1. **Ontology**：`wheel_skill_contract.ONTOLOGY` 描述轮辋、轮筒、中心盘、孔系、辐条、轮缘和背面，以及同轴、圆周阵列和连接关系。它是第一版领域词表，还没有实现通用图推理。
2. **Parameter Schema**：`WheelInputSpec` 校验六项可选规格；详细 CAD 参数与来源仍由 `WheelSpec` 和 `engineering_schema.WheelEngineeringDefinition` 管理。输入缺项保留为空，不从像素补出毫米数。
3. **Constraint Graph**：报告给出 PCD→螺栓中心半径、孔数→孔阵列、可见辐条→候选对称阶数、ET→安装面位置；CAD 约束由配方和几何检查执行。后续结构最小厚度和可加工圆角目前没有经过验证的规则，不在 V0.1 宣称覆盖。
4. **Evidence / Provenance**：输入图哈希、明确声明来源的规格、照片候选、规则估计、默认和未知分开；未声明规格来源保留 `unspecified`。置信度是候选指标，不是实际工程尺寸正确概率。
5. **Reconstruction Knowledge**：报告中的计划描述回转毛坯、窗口切削、造型候选、孔阵列及验证；真实构造操作见构建报告的 `stages`。STEP 可交换，但不包含原生可编辑 Feature Tree；编辑来源是配方和受控参数。
6. **Validation Knowledge**：构造、STEP、尺寸和外观分别报告；外观 IoU 不能替代实体、尺寸或工程审核。受控 Agent 动作仍由 `agent_cad.py` 的类型化计划和审批门处理。

## 准备等级

| 等级 | 本版含义 | 门槛 |
| --- | --- | --- |
| L0 Visual | 视觉候选或未验证 STEP | 可有 GLB、配方和待确认问题 |
| L1 Parametric Draft | 可由配方重建的 B-Rep/STEP 草稿 | 单一有效实体、STEP 回读通过 |
| L2 Dimension Constrained Draft | 六项关键规格来源于用户、图纸或实测，且进入构建并通过当前可用检查 | L1、六项规格与来源齐备、STEP 包络与孔系检查通过、ET 的构建剖面值与输入一致；目录规格或未注明来源不能升级，ET 尚非独立 STEP 测量 |
| L3 Geometry Checked Draft | 当前几何检查全部通过 | L2、无小碎面及已定义旋转检查通过；**不等于完整工程验证** |
| L4 Simulation Validated | V0.1 不提供 | 需独立 CAE 与责任人审核 |
| L5 Manufacturing Reviewed | V0.1 不提供 | 需材料、工艺、CAM、现场与责任人审核 |

## 参赛 Demo 的真实范围

在 DGX Spark 上展示时，用已获授权的照片输入，先显示来源/未知和“ET 未提供”的询问；用户补充 ET 后再以新运行生成配方与预览。复杂 B-Rep 构建目前可能耗时数十分钟，3–5 分钟路演应播放**明确标注为事先完成**且与同一输入哈希绑定的 STEP 构建记录，再现场打开 STEP、报告和逐项检查；不能把录制结果冒充即时生成。构建失败则展示失败与待处理项。不要预录一个 L3 标签来代表尚未通过的工程验证。视觉模型若在 DGX Spark 本地运行，保存节点、模型、端点和请求证据；单靠部署文档不算“全本地已验证”。

前后优化采用同一批照片和规格、相同内核与硬件，分列记录建模成功率、视觉误差、STEP 回读、尺寸检查与用时。详细门槛见 [Skill 评估契约](../.agents/skills/wheel-engineering/references/evaluation.md)。当前真实照片不在仓库，不能填写尚未复测的对比成绩。

本轮完成的**流程与证据修正**如下；这张表不代表照片拟合质量已经提高：

| 项目 | 修正前 | 修正后 |
| --- | --- | --- |
| Agent 发现 | 只有名为 `wheel_skill.py` 的程序 | 项目内可发现的 `wheel-engineering/SKILL.md`，明确输入、运行、验证与停点 |
| 参数来源 | 报告有部分 `spec/photo/default` 标记，六项原始规格来源不单独核对 | 可记录 user/drawing/measurement/catalog/unspecified；目录或未知来源不能提升到 L2 |
| 工程理解 | 问题和来源散落在报告字段 | 分组显示观测、已提供、估计、默认、未知及下一步决策；保留输入图哈希 |
| 默认网格结果 | 网格检查可能被算到 L1–L3 | 无 STEP 时固定 L0；STEP 回读缺失或失败也固定 L0 |
| 单次视觉验证 | 主要通过外部评测脚本查看 | `--visual-check` 可产前/斜视指标及叠加图，单独记录且不改变工程等级 |
| 运行复核 | 同目录可能残留旧 STEP 或预览 | 非空输出目录被拒绝；配方、模型及叠加图记录 SHA-256 |
| 模型请求 | 配置了视觉端点就可能调用 | `--use-vlm` 显式开启；默认使用需确认的造型预设 |

## 路线边界

比赛后优先补齐 Wheel Skill 的独立轮款验收、真正可执行的特征图、人工修正记录和真实轮毂尺寸/毛坯/CAM 数据。多品类 Skill、仿真优化和制造反馈属于后续产品阶段，不作为 V0.1 的完成条件。

## 本轮定型决策（2026-09-27）

本轮确认的正式产品名与参赛切口分别是 **Engineering Reconstruction Agent** 和 **Wheel Engineering Skill**。产品愿景是把不完整的真实参考资料转为可编辑、可追溯、可验证的参数化 CAD；首批用户为机械设计、CAD 与逆向工程人员，交付形态是协助工程师审阅和修改的 **Engineering Copilot**。黑客松阶段只证明轮毂这一种零件的 Understand → Reason → Reconstruct → Verify → Report 闭环。

三层架构与投入重点：

| 层 | 职责 | 阶段定位 |
| --- | --- | --- |
| Agent Runtime | 看输入、规划、调用受控工具、在失败时询问或停止 | 通用执行底座；DGX Spark 可作为本地模型运行目标 |
| Engineering Skills | 领域本体、参数、约束、证据来源、不确定性、重建和验证规则 | 当前重点与可积累的专业资产；首个实例是 Wheel Skill |
| Engineering Data Flywheel | 保存工程师修正、验证结果及未来制造反馈，支持后续 Skill 改进 | 长期目标；本版尚无真实修正/制造反馈闭环 |

四阶段路线：

1. **现在：Wheel Engineering Skill。** 证明少量照片和规格可以进入受控的工程理解、参数化草稿、验证与报告流程；每一项声称都绑定实际检查证据。
2. **近期：Engineering Reconstruction Agent。** 加入多视角、图纸和真实测量；让 Agent 在已有输入上主动询问、修改、对照和回退，同时保留人工确认。
3. **中期：Engineering Skill Platform。** 轮毂流程稳定后再扩展齿轮、支架、壳体等领域 Skill，共用 Agent Runtime 和证据契约。
4. **长期：Engineering Intelligence Platform。** 在获得真实工程修正、仿真与制造反馈后，才进入跨零件的设计优化和制造决策。

预期数据链为 Reference → Engineering Understanding → Constraints → CAD → Verification → Engineer Correction → Simulation → Manufacturing Feedback。**现在实际具备的是前半段的部分实现。** 工程师修正数据集、CAE 和制造反馈尚未建立，因此“数据飞轮”和“护城河”属于要验证的战略假设，不作为已实现功能对外陈述。

比赛演示控制在 3–5 分钟：上传正面图，显示看见的结构与无法观察的尺寸；用户补充 ET 等规格；显示重建计划、配方、实际 CAD 构建和逐项报告。前后优化只展示同输入、同验收规则的可复核差异，不用单张图的相似分数证明工程质量。主张是**可信的工程初稿**，不是从照片恢复所有真实尺寸，也不是制造发布。
