# 可执行母扇区程序：受控实验

2026-09-22。当前不更换 MiMo 或 CadQuery，不替换任何现有项目。
本实验先验证“按特征修改”是否真正改变指定 CAD 区域，并保留重建依据。
**这不是商品图拟合效果提高的证明。**

## 实现范围

`services/wheelcam/sector_program.py` 提供 `wheel-sector-program-v1`：

- `single / split_y` 两种受限拓扑，5–10 个旋转组，明确相位。
- 稳定特征 ID：主窗、分叉窗、最多两个根部狭长孔。
- 主窗使用根部/中段/末端半宽、径向跨度、偏转和圆头等少量控制。
- 根部孔使用径向位置、角度、长度和宽度；按 ID 修改，不重新提交整片像素坐标。
- `compile_sector()` 输出现有 `WheelSpec` 和编译清单；不另建第三套 CAD 内核。
- 清单包含程序哈希、编译器版本、特征与轮廓映射、采样检查及未知/未验证边界。

```python
program = example_program(family="split_y", groups=5)
candidate = patch_feature(program, "root_slot_left", {"width_mm": 8})
spec, manifest = compile_sector(candidate, base_spec, sources=project_sources)
```

`sources=None` 只用于独立设计夹具；未来接项目必须传真实来源。
人工、图纸、实测等确认字段仍锁定，批准候选不能解除证据锁。
输入不接受任意代码；未知字段、重复 ID、拓扑冲突、自交、相邻孔干涉、
过窄二维连接及进入中心保护区/轮辋连接区的轮廓被拒绝。

所有新特征当前明确为 `design_assumption`。曲面、厚度、背面和孔座仍继承基底模板；
不是完整自由曲面特征树，也未接入线上 Agent 工具或照片自动识别。

## 三份 STEP 的比较含义

运行命令（输出必须为新目录）：

```sh
PYTHONPATH=services:. .venv/bin/python scripts/validate_sector_program.py \
  --groups 5 --output artifacts/sector-program-five-v2
```

| 对象 | 输入 / 变化 | 要回答的问题 |
| --- | --- | --- |
| A 轮廓基线 | 与 B 相同的编译轮廓，直接送旧窗口 adapter | 固定几何基线 |
| B 程序重放 | 程序 JSON 反序列化后重新编译 | 能否忠实、可重复地重建同一实体 |
| C 指定孔编辑 | 只把 `root_slot_left.width_mm` 从 6 改为 8 | 是否只在该特征的旋转复制位置减材 |

A 与 B 的原始几何同源：这是接口等价/重放试验，**不是两种独立重建算法的胜负比较**。
固定尺寸、内核、拓扑和基面；关闭倒圆、拔模、脊线、浅槽来隔离变量。
这些 finishing 操作有其他构造测试，但尚未证明其局部编辑行为。

验收同时检查：

1. 三份真实 STEP 有效、单实体、包络正确、导出回读体积一致，已追踪操作没有降级。
2. A/B 实体双向差分低于显式数值噪声门槛。
3. C 仅减材；变化留在目标孔的旋转轨道内，中心区和外轮辋区没有变化。
4. 每个旋转副本均有相同减材量，其他特征和配方参数不变。
5. Boolean 差分本身有效，体积守恒且分区体积能回合；空值/无效结果不能冒充零误差。
6. 原生 cutter spline 相对密集设计轮廓的有限采样偏差 ≤ 0.1 mm。

0.1 mm 是此次**采样链路实验门槛**，不是工程公差、最小壁厚或严格 Hausdorff 上界。
4 mm 二维最小连接约束也不是轮毂强度标准。

第一轮原始 4 mm 圆头主窗在 192 点时偏差为 0.12393 mm，被门禁拒绝。
保持原始设计形状，只提高到 256 点后为 0.07716 mm；六组样例对应为 0.11516 → 0.07006 mm。
测试保留旧采样负控，不能用换一个更圆的形状或放宽阈值掩盖误差。
这只验证这些夹具；任意其他参数组合仍必须重新通过原生 CAD 检查。

产物包括程序、原始基底、编译结果、版本/哈希、STEP/GLB、检查报告与实际 STEP 投影图。
STEP 本身不包含原生参数化历史，必须同时保存配方和程序。

### 第一轮实际结果：候选未通过整轮验收

三份 STEP 均有效、单实体，并通过现有导出回读门；但进一步的整轮差分出现约
36,829.6 mm³ 体积守恒残差，不能宣布局部修改成功。
同一个 B STEP 的现有积分器在 eps=1e-7 / 1e-8 / 1e-9 分别给出约
5,001,230 / 5,395,712 / 5,395,712 mm³，跨设置差异达约 7.89%。
这说明同一积分器在导出前后相互吻合，仍可能重现同一个错误。

因此脚本新增体积积分稳定性门，失败即停止体积式差分验收，不放宽阈值；
保留 `experiment_passed=false`。复核原始 STEP 时不覆盖文件：

```sh
PYTHONPATH=services:. .venv/bin/python scripts/validate_sector_program.py \
  --groups 5 --reuse-builds artifacts/sector-program-five-v2 \
  --output artifacts/sector-program-five-review-v1
```

现有证据：`artifacts/sector-program-five-v2/failed-comparison.json`、
`artifacts/sector-program-five-review-v1/summary.json` 及 `comparison.png`。
复核先比对原配方和 STEP 哈希，再写入新目录。原始基线、历史工程模型均未替换。

独立诊断发现带分段积分的 Gauss–Kronrod 方法在已知胶囊几何上显著更接近解析体积，
因此进入下一轮验证器修复；网格体积仅作数量级交叉检查。第一轮尚未替换积分器。
后续已统一 `geometry._volume`、`preparation.volume` 和分色分区校验，详见
[统一计量](volume-measurement.md)。历史体积、重量、去除量不能据此追认为准确。
诊断表与可复现源码在 `artifacts/sector-volume-diagnostic-v1.json`；两个解析案例已作为自动化
正/负对照固化在 `tests/test_volume_integration_candidate.py`，不等于整轮局部编辑已通过验收。

## 后续换模型的比较协议

分开比较两件事，不混用结论：

- **换 AI 模型**：看结构识别、工具调用、局部修改计划和未知尺寸处理。
- **换 CAD 表达/内核**：看可表达形状、忠实执行、稳定性、局部修改和 STEP 交换。

建议下一轮冻结至少 8 个未参与调参的轮款、覆盖至少 4 种拓扑；同轮款图片不跨调参/保留集。
每模型使用相同图片、确认尺寸、工具白名单、提示、重试次数与 Token/时间预算；
执行“结构→IR”和“指定特征修改”两类任务，各重复至少两次。
记录每例原始响应、合法 IR、拒绝原因、最终实体及资源消耗；分列：

- 拓扑正确、未知尺寸保留、证据锁违规（必须为零）。
- 有效 STEP 成功、目标修改成功、非目标区域变化。
- 原图轮廓/特征误差；独立视角或人工核对标注用于独立评价。
- 延迟、调用成本、人工修正次数；固定预算与实际消耗同时记录。

没有独立几何真值时，不报告尺寸恢复精度；同图重投影不作为独立泛化验证。
当前没有运行这些 AI 对比，故不宣布替代模型胜出，也不自动下载本地模型或修改 provider。

## 下一步

先完成统一计量后的整轮差分及复合造型回归，排除近重合曲面 Boolean 分类异常，再将这些受控特征
接入 revision 绑定的 Agent 预览/接受流程，补齐可控截面及实际 CAD
相机渲染比较。已有照片模型继续作为基线，候选通过独立评价后才允许替换。
目前 `photo_fidelity=not_measured`、`engineering_approved=false`、`manufacturing_status=not_released`。
