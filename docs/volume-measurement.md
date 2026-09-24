# 统一体积计量与独立校核

2026-09-22。这是计量器修复，不是换 AI 模型、CAD 内核或轮毂造型。
输入证据、原模型及历史报告不覆盖；制造状态仍为 `NOT RELEASED`。

## 为什么替换积分方法

上一轮同一份 STEP 的普通 Gauss 体积随精度设置变化约 7.89%。更简单的、尺寸已知的
胶囊孔也复现了问题，且内核曾返回极小的自报误差：

| 6 mm 高、22 mm 长的胶囊 | 解析体积 mm³ | 旧 Gauss | 分段 GK 候选 |
| --- | ---: | ---: | ---: |
| 宽 6 mm | 745.646003 | 886.323632 | 745.646544 |
| 宽 8 mm | 973.592895 | 1097.967774 | 973.593003 |

新值与解析值的微小差异还包括实际 cutter 的周期样条近似，不全是积分误差。
负对照继续保留在测试中，旧诊断见 `artifacts/sector-volume-diagnostic-v1.json`。

## 统一调用路径

`mass_properties.py` 使用 `VolumePropertiesGK_s`，明确开启 `OnlyClosed=True`、
`IsUseSpan=True`，默认每面请求 `epsilon=1e-7`：

- `geometry._volume` 和 `inspect_shape`：几何/STEP 检查。
- `preparation.volume / checked_volume`：锻坯、卡钳干涉、重量与去除量。
- `appearance`：通过相同服务校核分色实体分区。
- 母扇区实验：同样的方法，并额外检查积分稳定性、实体差分和体积守恒。

无全局形状缓存，不偷偷退回旧算法或调整容差。无效或 null 几何、非有限/负误差、
过大的内核误差估计、非正实体体积会拒绝输出；真正的空 Boolean Compound 可以为零。
逐个实体验证正体积，避免反向实体与正向实体抵消；夹带游离面/线/点的混合容器也被拒绝。
原生 `PerformInfinitePoint(1e-7 mm)` 必须返回 OUT，不能用正体积或 CadQuery `isInside()`
替代这一检查；IN/ON/UNKNOWN 均拒绝。不过这仍不证明所有有限点分类或 Boolean 集合语义正确。

`epsilon` 是每面数值积分请求。内核返回的整体误差允许至 `max(10*epsilon,1e-9)`；
这是显式数值拒绝策略，不是独立证明的真实误差上界，也不是零件尺寸公差。
复杂局部差分仍须通过另一个 0.01 mm³ 守恒/非目标变化实验门，不能凭内核自报误差放行。

新报告保存 `volume_measurement`、`step_volume_method`、准备检查的 `volume_method`
以及分色的 `partition_check`。STEP 回读前后相同数字并不能单独证明绝对体积准确。
旧报告不补写新方法标签；界面明确提示旧体积、重量、去料比例需重建复核。

## 保持 Boolean 输入隔离

CadQuery Boolean 默认直接传递原生子形状，不能假定原输入不可变。
卡钳、毛坯、分色和实验差分分别复制每个操作数；比较前测量原始实体，避免上一次
运算改变下一次测量的输入。窗口浅槽的原生有向体积仅用于正/负组件分类，未被直接
替换成禁止负值的公开计量接口，也不作为重量或减材量。

复杂整轮差分另外复现了 Boolean 分类问题：默认运算返回的 common 将中心孔内一点判为
内部，且无穷远点为 IN，尽管 `isValid()` 为真。诊断显式使用只读副本、非破坏模式、
串行运算与 `fuzzy=1e-6 mm` 后，差分拓扑恢复为 5 个根部小实体和空 added；
这只改变派生比较运算的容差，**不修改源 STEP**，但可能改变派生边界/拓扑，不能称为精确等价。
默认积分请求下最大守恒残差仍有 0.0280452 mm³，超过原 0.01 mm³ 门禁，不能宣布局部修改验收成功。
诊断保存在 `artifacts/sector-delta-diagnostic-gk-v1/` 与
`artifacts/sector-delta-deterministic-fuzzy1e6-v1/`，不全局更改生产构造的 Boolean 策略。

## 独立参考与适用范围

`validate_volume_oracle.py` 不调用 OCCT 体积算法，也不读取旧报告的体积值。
它将解析胶囊分成矩形与两个半圆，在已知回转毛坯厚度上做二维 Gauss–Legendre 积分。
仅适用于一个根部孔的宽度增加：长度、位置、其他特征不变，且无倒圆/拔模/浅槽等修饰。
孔必须处在未受中心盘、螺栓孔、轮辋影响的毛坯区域；其他情形明确拒绝。

五组固定样例的预期减材量为 **5012.720369528511 mm³**，96/192 阶积分差约
4.55e-12 mm³。这独立于 OCCT 积分，但仍依赖同一套假设设计，不是实物测量真值。
实际周期样条与解析胶囊不完全相同，参考对比单独声明 0.5 mm³ 数值/表示允许量；
该允许量不替代整轮差分的 0.01 mm³ 门槛，也不是制造公差。

复核旧 STEP 时校验其 STEP/配方哈希、最终构建配方和程序重放；当前重新计量与历史
构造报告分别记录。读取旧 STEP 不冒充新算法下重新导出/回读；后者由实际导出回归负责。

```sh
PYTHONPATH=services:. .venv/bin/pytest -q tests/test_mass_properties.py tests/test_volume_pipeline.py
PYTHONPATH=services:. .venv/bin/python scripts/validate_sector_program.py \
  --groups 5 --reuse-builds artifacts/sector-program-five-v2 --output artifacts/sector-program-five-gk-v2
PYTHONPATH=services:. .venv/bin/python scripts/validate_volume_oracle.py \
  --source artifacts/sector-program-five-v2 \
  --measurements artifacts/sector-program-five-gk-v2/volume-measurements.json \
  --output artifacts/sector-volume-oracle-gk-v1
```

每次执行必须选新输出目录。实际执行结果与仍未通过的门禁以 `validation.md` 为准，
不从解析测试通过推导照片复刻准确、整轮工程安全或可加工。
