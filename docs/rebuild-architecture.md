# WheelCAM 架构重置：从视觉网格回到工程 CAD

日期：2026-09-22

最新实施补充：[工程一致性第一批修正](foundation-consistency.md)。几何通过和满足请求分别统计，
缩小圆角等降级构建不能计入精确请求验收；新增 operations 套件覆盖窗口等构造路径，
不替代下文的 legacy 60 例复验或独立照片测试。运行必须使用新/空输出目录以保留旧证据。

Agent 的职责、受控绘图/修改协议、三层 CAD IR 和成熟度状态见
[Engineering Reconstruction Agent V1](engineering-reconstruction-agent.md)。Agent 是主动建模者，但所有动作必须经过类型化 IR、revision、批准策略和几何验证。

## 决策

WheelCAM 的产品主线不再定义为“商品图直接生成一个看起来相似的 3D 网格”，而定义为：

> 视觉逆向工程 + 参数化 CAD + 几何约束 + 验证闭环。

`sector-study`、单图深度和通用 image-to-3D 结果保留为研究证据与失败对照；它们不能直接成为工程主模型，
也不能把像素相似、封闭网格或单一实体当作尺寸正确、STEP 可编辑或可制造的证明。

## 当前资产盘点

| 模块 | 当前状态 | 新路线处理 |
|---|---|---|
| CadQuery/OpenCASCADE 参数化实体 | 已能生成轮辋、中心盘、孔、辐条阵列和布尔实体 | 保留，作为 P1 主干 |
| STEP 导出与回读 | 已检查单实体、有效 B-Rep、体积差和包络 | 保留，扩大参数矩阵 |
| `WheelSpec` | 有尺寸范围和组合约束，但“默认值”容易被误读为已知值 | 外挂 `wheel-engineering-v1` 来源/置信度/UNKNOWN 契约 |
| `features.json` | 有工程语义和候选工序，但仍是交接草案 | 后续与稳定 Feature Tree 对齐 |
| 照片轮廓、局部曲面、PartPacker | 能提供候选或研究结果，不能恢复不可观测工程尺寸 | 冻结为实验输入，必须经参数层和人工确认 |
| CAM/CAE/制造发布 | 尚未成立 | 保持 `not_released` |

## 新的强制数据流

```text
照片 / 图纸 / 用户规格 / 实测
              ↓
   wheel-engineering-v1
   value + unit + source + confidence + constraint
              ↓
        Design Intent / Feature Tree
              ↓
       WheelSpec 参数化求解
              ↓
       CadQuery/OpenCASCADE B-Rep
              ↓
  STEP 回读 + 几何检查 + 工程未决项
```

图像模型只允许产生 `OBSERVED` 或 `INFERRED` 候选，不得直接写 STEP，也不得覆盖 `SPECIFIED`/实测数据。
无法从输入确定的背面、壁厚、安装面和孔深必须允许为 `UNKNOWN`。

## 分阶段门槛

### P0：工程参数空间

- 完成轮辋、中心盘、孔系、辐条、轮辐连接、背腔和轮辋槽的参数分组。
- 每个参数包含 value、unit、min/max、constraint、source、confidence。
- 来源固定为 `observed / specified / inferred / default / unknown`。
- `UNKNOWN` 必须是空值和 0 置信度；默认值是造型假设，不等于 UNKNOWN，也不等于已确认。
- 每次 STEP 导出同步生成 `engineering.json`，制造状态固定为 `not_released`。

### P1：参数化 CAD Engine

验收目标不是单张图片相似，而是至少 50 个覆盖边界与组合的参数案例全部满足：

1. OpenCASCADE `isValid=true`；
2. 恰好一个 Solid；
3. 正体积、包络尺寸匹配；
4. STEP 导出后重新读入仍为一个有效 Solid；
5. 体积相对差不超过 `5e-5`；该值覆盖已观测的普通放样 2.5543e-5 和复杂双脊面 3.07e-5，仍须在矩阵报告中逐例保留真实差值；
6. Feature Tree 和 engineering provenance 与 STEP 哈希绑定；
7. 所有案例仍为 `not_released`，不冒充强度或加工批准。

### P2–P4：合成数据与二维视觉

只有 P1 达标后，才从参数化 CAD 批量生成 RGB、深度、法线、分割、轮廓、相机和参数真值。
第一视觉里程碑限制为“正视图 + 一个已知尺寸 → 二维参数化草图”，不预测背面和不可观测厚度。

### P5 以后

再依次进入三维参数预测、Design Intent、CAD↔Render 优化、多视图融合和工程验证。
任何阶段都不能用视觉相似度代替尺寸、公差、材料、疲劳、冲击和法规验证。

## 当前执行清单（以后继续工作从这里开始）

当前证据：

- [x] 旧的深度图、Sector Study 和 PartPacker 路线已降级为实验，不再作为工程 CAD 主线。
- [x] 已建立 `wheel-engineering-v1`、参数来源/置信度和 `engineering.json` 输出。
- [x] 已定义确定性的 60 案例 P1 参数矩阵。
- [x] 已完成 3 个案例的 STEP 导出/回读冒烟验证，3/3 通过。
- [x] 当前完整回归测试为 183 passed，前端构建通过。
- [x] 已执行首次完整 60 案例 P1 验证：52/60 通过，8 例因 STEP 回读体积变化超限而失败。
- [ ] 已加入 STEP 稳定性驱动的连接圆角降级并验证原失败案例 4/4；其余 4 例和完整 60 案例复验因人工暂停而未完成。

下一步严格按以下顺序推进：

1. **跑完 P1 全量矩阵。** 执行：

   ```bash
   PYTHONPATH=services:. .venv/bin/python scripts/validate_parametric_matrix.py \
     --limit 60 \
     --output artifacts/parametric-matrix-v2
   ```

   保存每例 STEP、报告和失败原因；若有失败，修几何引擎和约束，不为通过测试而随意放宽门槛。

2. **拆分稳定的领域对象。** 将 `WheelSpec` 逐步拆成 Rim / Hub / BoltPattern / Spoke，并保留兼容适配器，避免破坏已有数据和 API。

3. **补齐 UI 工程参数面板。** 明确显示 Observed / Specified / Inferred / Default / Unknown、置信度和关键未知项；Manufacturing Release 必须独立显示且不可被视觉结果误触发。

4. **只有 P1 达到 60/60 后才进入 P2。** 从参数化 CAD 生成 RGB、Depth、Normal、Mask、Edge、Camera、Parameters、B-Rep 和 STEP 成套合成数据。

5. **P3 的首个视觉里程碑只做二维。** 输入一张正视图和一个已知尺寸，恢复圆心、轮圈、PCD、螺栓孔、单辐条轮廓、对称关系和可编辑的 2D 参数化草图。

6. 后续才依次做 Camera/Scale、3D 参数预测、Design Intent、逆渲染优化、多视图融合和工程验证。

长期约束：

- 不直接做 Image → STEP 黑盒生成。
- 不把视觉相似、封闭网格或单实体等同于工程尺寸正确。
- 单图不可见的背面、壁厚、孔深等允许输出 `UNKNOWN`，不能偷偷补默认值并伪装成观测结果。
- 没有真实机床、刀具、夹具、后处理、CAE/疲劳和法规验证证据时，状态始终保持 `NOT RELEASED`，不得进入 CAM/CNC 制造发布。
