# 轮毂窗口标注工具

给评价基准用：在照片上人工标出外圈、中心盘、窗口（辐条之间的开口）和忽略区，替代“SAM 生成、SAM 自评”的做法。背景见 [窗口法小验证](../window-spike/README.md)。

## 启动

```bash
.venv/bin/python experiments/annotate/serve.py "/Users/happyding/Desktop/轮毂"
```

浏览器打开 `http://127.0.0.1:8770/`（只监听本机）。

- 照片目录**只读**，不改原图。
- 标注按图片 SHA-256 保存到 `data/annotations/<sha256>.json`（`data/` 不进 git）。改名或重复拷贝的同一张图共用一份标注。
- 可用 `--out` 另指目录，`--port` 换端口。
- 工作台（v10）读的是同一目录（`data/annotations/`，或 `WHEELCAM_LABEL_DIR` 指定的目录）。在工作台上传同一个图片文件，点击“窗口标注 · 拟合窗口”，就能按这份标注拟合窗口法轮廓。见 [docs/photo-fitting.md](../../docs/photo-fitting.md)。

## 标注什么

| 工具 | 键 | 操作 |
| --- | --- | --- |
| 外圈 | 1 | 沿轮唇外边均匀点 ≥5 点，回车拟合带旋转的椭圆；斜视照片也适用 |
| 中心盘 | 2 | 沿中心盘外缘点 ≥5 点，回车拟合；其内不计分 |
| 窗口 | 3 | 每个开口一个多边形，沿辐条**正面**可见边界点，回车或双击闭合。后面看到背景或轮辋内壁都算窗口 |
| 忽略 | 4 | 遮挡、支架、强反光等看不清的区域，不计分 |
| 选择 | 5 | 选中后拖动顶点或按 Delete 删除 |

其他快捷键：滚轮缩放，右键或空格拖动，<kbd>F</kbd> 适配，<kbd>Backspace</kbd> 撤销上一点，<kbd>Esc</kbd> 放弃当前多边形，<kbd>S</kbd> 保存，<kbd>←</kbd><kbd>→</kbd> 切换（有改动自动保存）。

右侧填写结构（单片 / 两片 / 三片）、视角、背景、辐条类型、组数和“可用于评价”，用于筛选基准集。

## 标注格式

`wheel-window-labels/v1`，坐标为 EXIF 方向校正后的原图像素：

```json
{"format": "wheel-window-labels/v1",
 "image": {"name": "…", "sha256": "…", "width": 1280, "height": 1707},
 "rim": {"cx": 0, "cy": 0, "a": 0, "b": 0, "angle_deg": 0, "rms_px": 0, "points": [[x, y], …]},
 "hub": null,
 "windows": [{"points": [[x, y], …]}], "ignore": [],
 "meta": {"structure": "单片", "view": "正面", "usable": true},
 "saved_at": "…"}
```

## 生成遮罩与评分

```bash
.venv/bin/python experiments/annotate/labels_to_mask.py data/annotations/<sha>.json \
    --size 720 540 --out artifacts/labels --candidate v9_mask.png proto_mask.png
```

- material = 外圈椭圆内 − 窗口；评价区 = 中心盘椭圆到外圈 × 0.97 之间 − 忽略区。
- 输出材料 IoU、精确率、召回率，只衡量这张照片上已标注的正面开口，不代表尺寸精度。
- 候选遮罩须与 `--size` 同一像素坐标系，例如识图使用的 720 px 画面。

## 旋转窗口基准

```bash
cd experiments/annotate && ../../.venv/bin/python benchmark.py [--all] [--json out.json]
```

对每张勾选“可用于评价”且填了辐条组数的标注，依次：

1. 用外圈椭圆把照片校正成正圆（弱透视假设）；
2. 在极坐标下按组数折叠；
3. 只用偶数扇区生成窗口模板，在奇数扇区上算 IoU（按面积加权，忽略区不计）。

- 这个分数是“同一个窗口形状 × N 次旋转”在这张照片上**能达到的上限**。
- 失分可能来自：
  - 造型本身不对称；
  - 凹面视差，椭圆校正消除不了；
  - 标注误差。
- 要定位是哪一组偏了，看 `per_sector_loo`（留一法：每组对照其余各组的模板），均值为 `loo_iou_mean`。
- `per_sector_iou` 里的偶数组参与了建模板，分数偏高，不能用来定位。
- 它不是 CAD 结果。CAD 候选遮罩另用 `labels_to_mask.py` 评分，拿来和这个上限比。

`test_benchmark.py` 用合成的倾斜五组轮毂检查：完全对称时，留出扇区 IoU 应接近 1；改坏一个窗口，只有那个扇区的分数下降。

## 边界

- 斜视照片里，窗口后面能看到轮辋内壁；而当前 CAD 投影遮罩包含整个实体（含轮辋），会把这部分算成材料。斜视照片应只投影轮辐面（排除轮辋实体）后再评分，这一步尚未实现。
- 单人标注，没有一致性检查；同款不同角度需人工分组，避免拟合集和保留集混用。
