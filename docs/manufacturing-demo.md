# M59 工程图、加工准备与仿真演示

## 加工级 STEP 同源版本（当前）

M59 的 `stock.step` 与 `machining.step` 位于 `runs/real-orders-eval/machining/case-03-d20w10.5/`。以下命令直接回读这两份 STEP，并使用同目录的 `recipe.json` 和 `machining_report.json` 生成窗口参考轨迹、刀具表及采样去料仿真；不会重新生成另一套毛坯或目标零件。

```bash
PYTHONPATH=services .venv/bin/python scripts/build_manufacturing_step_demo.py \
  --machining-dir runs/real-orders-eval/machining/case-03-d20w10.5 \
  --spec runs/real-orders/case-03/d20w10.5/spec.json \
  --out runs/m59-manufacturing-step-demo
```

输出在 `runs/m59-manufacturing-step-demo/`：`process_plan.json` 记录输入 STEP 的路径和 SHA-256，`stock_step.glb`、`machining_step.glb`、`stock_after_roughing.glb` 及 `simulation_3d.png` 显示同一对 STEP 的离散化和采样去料。`drawing.svg` / `drawing.html` 从同一份 `machining.step` 的离散化实体生成；技术要求列出加工级报告的锥座调整和未进入 STEP 的辐条造型曲面。若另有完整造型 B-rep 的 `report.json`，可用 `--forged-report` 加入其 `forged.skipped_operations`。`reference.nc` 里的 G81 钻孔及窗口栅格运动**全部是注释**，坐标按 STEP 的轮辋宽度中面 Z=0、正面 +Z；窗口轮廓取自加工级 STEP 构建器的 `window_loops`。车削轮廓必须由工厂 CAM 直接取 `stock.step`，这里不再输出另一套网格轮廓的伪车削轨迹。

M59 的 STEP 回读为有效单实体。独立三角化后的两份 STEP 在曲面拼接处有约 3894 mm³ 基线差；仿真将它单独报告，窗口刀具带来的**新增采样过切为 0 mm³**，与加工级目标相比仍有约 0.382 L 余量（其中包括未执行的孔和锥座工序）。这是采样去料对比，不是机床、刀柄、夹具碰撞或可上机 NC 的验证。加工级 STEP 仍缺辐条曲面等精加工几何；锥座深度调整也在源 `machining_report.json` 标为待工程师确认。整体保持 `not_released`。

## 早期网格演示（仅作对照）

本演示从既有 M59 配方与脱敏确认单生成**单独的本机候选**；不修改 Spark 正在评测的配方或 `wheel_skill.py`。确认单孔型 `15X32X60` 覆盖旧配方的 22 mm 模板孔。输出仍是 L0 网格候选，`manufacturing_status=not_released`。

```bash
PYTHONPATH=services .venv/bin/python scripts/build_manufacturing_demo.py \
  --recipe runs/m59-v1/recipe.json \
  --spec runs/real-orders/case-03/d20w10.5/spec.json \
  --out runs/m59-manufacturing-demo
```

输出包括 `drawing.svg`、可打印的 A3 `drawing.html`、`wheel.glb`、`demo_report.json`、`tool_list.csv`、`process_plan.json`、`reference.nc`、`simulation.png`、`simulation_3d.png`、`stock_assumed.glb` 和 `stock_after_roughing.glb`。图纸 PDF 可从 HTML 按 A3 横版、无页边距打印；本机演示的 PDF 单独保存。生成器拒绝与确认单 PCD、孔数、中心孔不一致的配方，纠正孔型后重新建网格并检查实体、包络、孔系、ET 和旋转约束。

`reference.nc` 列出车削回转轮廓、G81 钻孔位置、锥座工序和 Ø6 刀具的全部窗口分层栅格轨迹。G81 的 R 是孔位正面入口轮廓前方 5 mm，Z 是安装面背侧再穿出 3 mm；它不借用窗口粗加工的深度。**所有运动行是注释，文件不能上机执行。** 机床、坐标系、夹具、刀具长度、切削参数和后处理均未确认。工序单也把曲面精加工留给工厂 CAM。

`simulation.png` 用 1.5 mm XY 网格展示窗口目标、Ø6 刀具扫掠区和未覆盖面积。二维目标与三维扫掠截面都限制在 `window_r_out` 内，避免把轮辋筒壁作为窗口材料切掉。两个 GLB 则把连续刀具扫掠体从**模型自身的假定回转毛坯**中布尔去除，与设计网格对比，报告残料和切入设计实体的体积。`gouge_vs_design_mm3 < 1` 时标为 `sampled_no_gouge`，剩余体积交工厂 CAM 曲面精加工；否则为 `requires_replanning`。它仍不是实际锻坯、夹具、刀柄与机床的碰撞或去料验证。订单只有毛坯型号，没有毛坯三维几何。任何图纸、刀具表和轨迹均须由工程师与原始工厂 CAD 对照后审核。
