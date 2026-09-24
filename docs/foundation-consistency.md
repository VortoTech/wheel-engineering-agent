# 工程一致性第一批修正

2026-09-22。目标是修复后续重建/Agent 优化赖以成立的数据契约，不宣称外观重建或制造验收完成。

## 1. 输入证据不可被 Agent 覆盖

`manual / drawing / measurement`（兼容导入的 `specified`）来源被锁定。
Agent 的 `set_parameter / replace_sketch / mark_unknown` 均不能覆盖这些字段：
即使数值相同、模型置信度更高，或者 action 被批准，也不允许替换原来源。
整批计划在预览阶段拒绝，API 返回 409 `confirmed_evidence_locked`；项目、revision 和审计记录不变。
Agent 可以提出复测问题；用户若确需纠正输入，使用明确的人工草稿编辑流程。
工程导出保留用户显式提供的置信度，不再把 measurement 自动升级为 1.0。

## 2. 请求、最终配方、实际构造结果分开

`report.json / engineering.json / features.json` 共享 `wheel-build-resolution-v1`：

- `requested_spec`：原始构建请求，绑定原始证据来源。
- `resolved_recipe`：STEP 稳定性降级后选中的最终生成器输入。
- `feature_results`：内核报告的分位置连接圆角、窗口倒圆覆盖、凸脊、显式支臂棱边和背腔截面深度。
- `status`：`exact / degraded / unverified`，与实体/STEP 几何检查独立。

`exact` 只表示已追踪的构造操作未报告调整，不是全模型计量，也不是工程批准。
窗口后边缘采用模板限制的倒圆值；不能用一个前缘圆角数字表达所有边。
当前窗口拔模由轮廓到质心的径向缩小近似，尚未证明恒定法向拔模角，因此开启时列为 `unverified`。

导出工程记录移到最终 STEP 候选确认之后；降级后的几何描述、预览和工艺候选使用最终配方。
`engineering.parameters` 及 `features.spec/sources` 仍表示原始输入，不将降低后的值伪装成实测。
UNKNOWN 输入可以保留概念构建假设，但不会因为成功构建而变成已知尺寸。
旧报告缺少该契约时，界面提示尚无逐特征记录，不反推为 exact。

## 3. 窗口拟合与 CAD 同源基面

`template.window_blank_profile()` 同时提供 CAD 回转和视觉拟合的二次 Bezier 控制点。
窗口拟合在尚无窗口草图、输入仍为 loft 时，也明确求值窗口基面；包含 `spoke_crown_mm`。
普通 WheelSpec / CAD 校验没有被绕过，缺少窗口的 spec 仍不能生成窗口实体。

验证比较实际 OCCT 曲线点和回转实体表面距离，不只比较两套相似公式。
基面仍不包含局部凸脊、浅槽、圆角、中心盘和轮辋；真实完整 CAD 渲染评价尚待实现。
`spec_for()` 对特殊窗口输入的 loft 验证载体限制仍待独立改造。

同图各组参与过相机、相位和异常组选择，原 `held_out_iou*` 仅为兼容保留；新增
`within_image_consistency_iou / metric_scope`，界面不再称其为独立留出检验或可达精度上限。
历史照片分数未重算，不能据此声称新模型提高了原图相似度。

## 4. 构造验收与后续边界

参数矩阵保留 legacy 参数接口，新增 8 例 operations 套件，覆盖 single / paired / window、
relief / ridge / draft / rim pockets。用人工设计的合成窗口测试构造路径，不冒充陌生轮款测试集。
报告分离 geometry_passed、exact_requested、degraded、unverified；脚本拒绝覆盖非空验收目录。

```sh
PYTHONPATH=services:. .venv/bin/pytest
PYTHONPATH=services:. .venv/bin/python scripts/validate_parametric_matrix.py --suite operations
npm run build
```

实际运行证据见 validation.md。旧 60 例全量验收尚未完成复验，新增 8 例不是替代它。

下一批工作：可执行母扇区特征图和受约束曲线/截面 → 最终 CAD 的统一相机渲染/评价 →
候选版本接受/回退 → 按轮款隔离的独立基准。现有静态 Feature Tree 只是描述性摘要。

后续进展见 [母扇区受控实验](master-sector-program.md)：已加入有限语义开口与局部编辑，
但实际 STEP 比较暴露现有体积积分不稳定，整轮验收保持失败。下一优先项改为先验证并修复
`geometry._volume` / `preparation.volume` 的同源计量；单实体、STEP 回读相符不能证明绝对体积准确。

模板内仍有尚未全部参数化的轮辋/孔座/背部常量，最小壁厚、曲率连续性、实际毛坯、
CAM/CAE 和制造发布均未完成；状态继续为 `NOT RELEASED`。
