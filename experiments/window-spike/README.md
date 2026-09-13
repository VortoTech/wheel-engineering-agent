# 窗口法小验证（2026-09-13）

验证两件事：

1. **SAM 2.1** 能否从照片分出辐条区域；
2. **「车削回转坯 − 窗口」** 建模能否比 v9 的「截面放样拼辐条」更贴近照片，并且做得出圆角。

这是一次性实验代码，不进产品路径。决策与完整记录见知识库 `docs/projects/wheel-cam/fidelity-2026-09.md`。

## 结果

参考照片 `8053104275…`（8 组双辐、分体式），项目“直顺辐条优化”、模型版本 `7936b250…`。基准为 SAM 遮罩（人工目视核对，**非独立标注**），评价区为外圈椭圆半径 0.30–0.76 的环带。

| | 整体 IoU | 偶数组（拟合） | 奇数组（留出） | 连接圆角（请求 3 mm） |
| --- | --- | --- | --- | --- |
| v9 放样 | 0.509 | 0.464 | 0.559 | 0 mm |
| 窗口法原型 | 0.611 | 0.548 | 0.681 | 3 mm / 3 mm |

原型为单一有效实体；STEP 回读体积差 2.5e-6；窗口棱边 2 mm（正面）/ 1 mm（背面）倒圆全部成功；构建约 150 s。

**局限**：

- 原型直接拟合同一份 SAM 遮罩，比较对它有利；奇数组只是旋转对称副本，不是独立样本。
- 原型辐臂偏粗；窗口外端越过轮唇内缘，辐尖有尖角；没有拔模，中心盘仍是平盘。
- 只有这一张照片。

## 复现

所有脚本在**工作目录**里读写（`context.json`、各遮罩、STEP、报告），建议用 `artifacts/window-spike/`（不进 git）。

```bash
mkdir -p artifacts/window-spike && cd artifacts/window-spike
P=../../.venv/bin/python; X=../../experiments/window-spike

$P $X/v9mask.py [JOB_ID]                  # 取出 720 px 照片、相机与 v9 投影遮罩 → photo720.png context.json v9_mask.png
$SAM $X/sam_segment.py facebook/sam2.1-hiera-small   # 生成点击提示 sam_prompts.json（整体遮罩本身不可用，见下）
$SAM $X/sam_arms.py                       # 每根辐臂一个对象 → sam_arms.png
$P $X/fit_windows.py sam_arms.png         # 偶数组折叠拟合 → windows.json
WINDOWS=windows.json $P $X/proto_build.py 2.0 0      # 原型 CAD → proto.step proto_mask.png proto_report.json
$P $X/evaluate.py sam_arms.png v9_mask.png proto_mask.png
$P $X/overlay.py sam_arms.png v9_mask.png proto_mask.png   # → compare.png
```

`$SAM` 是单独的 Python 环境，避免改动项目的 `uv.lock`：

```bash
uv venv --python 3.12 /path/to/samenv
VIRTUAL_ENV=/path/to/samenv uv pip install torch torchvision "transformers>=4.56" pillow numpy scipy accelerate
SAM=/path/to/samenv/bin/python
```

实测版本：transformers 5.17、sam2.1-hiera-small，Mac 上 CPU 与 MPS 结果一致。

## 踩坑

- **SAM**：一次给几十个点分整个轮心，结果是满屏噪点（置信度约 0.003）。改为每根辐臂一个对象、只保留单臂大小的遮罩（面积小于画面的 0.8%）再合并，才可用。下方窗口仍有漏分割。
- **布尔**：窗口只切辐条坯、再与中心盘合并时，运算**静默失败**：求交为 0，结果成两个实体，`BRepAlgoAPI_Check` 也不报错。先合并中心盘与辐条坯（同轴回转体），再切窗口，才得到单一实体。
- **倒圆**：
  - 把回转接缝边、中心盘附近的边一起选进来，整批失败；只选窗口棱边（BSPLINE，且半径大于中心盘半径 + 6 mm）即可。
  - 倒圆失败时不能直接取 `Shape()`，要先检查 `IsDone()`，否则 OCC 段错误。
  - 棱边不止两个邻面时 `Add()` 直接抛异常。项目里的 `geometry._fuse_with_fillet` 没有防护，`proto_build.py` 在外面包了回退。

## 下一步

用 [标注工具](../annotate/README.md) 建多张照片的人工标注基准，替代 SAM 自评，再决定是否把窗口法做成模板 v10。
