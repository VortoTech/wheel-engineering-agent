# DGX Spark 全链验收：脱敏 M59

2026-09-28。本队已授权的 DGX Spark GB10 节点；隔离目录中执行，原有 `lp-vllm` 服务未重启或改配置。使用节点上**已有**的脱敏 M59 正面/斜视图、确认单规格和匿名 CAD 真值；工厂 CAD 只参与最后对照。没有重新上传订单数据、环境文件或凭据。

## 应用在 Spark 主机运行

代码对应提交 `1d9d57d`（后续提交 `42409ce` 只加入视频脚本），与节点已有副本不同的代码和页面文件按 SHA-256 核对后同步到独立目录。用节点已有 Python 3.12 虚拟环境、CadQuery/OCP、manifold3d、`step3-vl-10b-fp8` 模型运行一条 `scripts/demo_chain.py` 命令。模型经节点回环地址调用，长任务放在 tmux。`chain.json` 和全部生成物已取回本机 `runs/spark-m59-20260928/`，此目录不进入 Git。

| 步骤 | Spark 实测 | 主要证据 |
| --- | ---: | --- |
| 照片重建 + 视觉造型 Agent | 432.2 s，PASS | 5 辐；外圈盲窗 0→15；窗口斜面宽 16→4 mm、深 22→6 mm；网格检查无失败项；`reconstruct/style/style_agent.json` |
| 加工级 STEP | 18.1 s，PASS | `machining.step` 与 `stock.step`；单一有效实体、STEP 回读、无碎面、ET 检查均 PASS |
| 加工包与采样仿真 | 3.0 s，PASS | `process_plan.json`、刀具表、注释参考 NC、仿真图；采样新增过切 0 mm³，待精加工余量 0.384 L |
| 工程图 | 0.4 s，PASS | `drawing.svg`；节点无 PDF 转换器，本次未生成 PDF |
| 工厂 CAD 对照 | <0.1 s，PASS | 外径、ET、PCD、孔数、孔径、中心孔误差 0；总宽误差 −0.26 mm；轮辋截面中位误差 1.98 mm |

整链 **5/5 步成功，453.7 s**。`manufacturing_status=not_released`。锥座深度从模板 22 mm 上提至 12.3 mm，以保留 8 mm 直孔；报告要求工程师确认。加工级 STEP 不包含辐条正面曲面、脊线、窗口斜面和外圈盲槽，不能作为完整造型或直接制造文件。当前仿真只证明这次 1.5 mm 栅格采样下没有新增过切，不覆盖刀柄、夹具、机床碰撞或试切。

### 可追溯摘要

| 对象 | SHA-256 |
| --- | --- |
| 脱敏正面图 | `fe7dae90727ef9cc6769528e0c012b0a175d809fac47b7ab0ced45bfcc91bdfe` |
| 脱敏斜视图 | `020999bdf7c4904b95dc93322ff15f2b6253ce1544cf92d779e093f599219ea8` |
| 脱敏规格 | `b7b34c3d55fdbcc0362463fdca7e6fc04768f959e07ce3cb7b212d2f65852828` |
| 演示链脚本 | `9c5ac6c14e8d28b7ed50cb967470c314ce192a815876420595c3c2ed0c259605` |
| Wheel Skill | `a624b2607699234bc72c084a46de437a7d3a8b700cbb473a773ac138506772c0` |
| `chain.json` | `908f89857888678ae68522dd2d000e1055c5b020fe21ef92109b849e4121f2db` |
| `machining.step` | `0104b0baa2d2f0740220a56e7c2eba6f4eea31cbb28b35aaae83335609c86dde` |

## F9：容器应用 + Spark 本地模型

另用仓库的 `Dockerfile.spark` 和 `.dockerignore` 在同一 Spark 节点构建了 ARM64 应用镜像。镜像 ID 为 `sha256:668410682ed48c24e6c93b3f9876c9a8ea783f7426fd8d09b47def975f9afcaf`。应用容器以节点普通用户、只读根文件系统运行；脱敏 M59 输入只读挂载，产物写入独立输出目录；通过 host 网络的 `127.0.0.1:8000` 调用节点现有 StepFun 模型。没有停止或更改模型服务，也没有开放公网端口。

容器版**另一次**全链结果位于本机 `runs/spark-m59-container-20260928/`：

| 步骤 | 容器版实测 |
| --- | ---: |
| 重建与造型 Agent | 377.9 s，PASS；5 辐、15 盲窗，斜面改为 4/6 mm |
| 加工级 STEP | 18.6 s，PASS；单实体、STEP 回读、无碎面、ET 检查均通过 |
| 加工包与采样仿真 | 3.2 s，PASS；新增采样过切 0 mm³，余量 0.384 L |
| 工程图 | 0.4 s，PASS；SVG 存在，PDF 未生成 |
| 匿名工厂 CAD 对照 | <0.1 s，PASS；轮辋截面中位 1.98 mm，P90 **7.11 mm**，总宽误差 −0.26 mm |

**5/5 步成功，总计 400.2 s**；`chain.json` SHA-256 为 `d77a88f7e0485dad9d34631f74192fa9cf028e39623f3c277c064c355f7c908e`。容器 STEP 与虚拟环境 STEP 的字节 SHA 不同；两者重新导入后的几何摘要相同：有效单实体、55 个面、体积 6,781,921.04 mm³、包围盒 547.8 × 547.8 × 294.0 mm。容器 STEP SHA-256 为 `b2341983de1c451e50462aef8fd7d5c64462bb32aaca31f55a3590a9c9a1e889`。这证明当前检查和几何摘要一致，不声称两个 STEP 文件逐字节相同。

工作台也在同一应用镜像内做了短时回环验收：`/api/runs` 返回 M59，页面和真实 GLB 文件均为 HTTP 200，监听仅在 `127.0.0.1`。验收后已停止临时工作台容器。

在 Spark 上以已授权的脱敏案例复现（先创建空输出目录的**父目录**；下列变量由操作者指向自己的本地路径）：

```bash
docker build --network host -f Dockerfile.spark -t wheelcam-app:demo .
docker run --rm --network host --read-only --tmpfs /tmp:rw,size=2g \
  --user "$(id -u):$(id -g)" --cpus=12 --memory=24g \
  -e HOME=/tmp -e XDG_CACHE_HOME=/tmp/cache -e MPLCONFIGDIR=/tmp/mpl \
  -e WHEELCAM_VLM_BASE_URL=http://127.0.0.1:8000/v1 \
  -e WHEELCAM_VLM_MODEL=step3-vl-10b-fp8 \
  -v "$CASE_DIR:/orders/case-03/d20w10.5:ro" -v "$OUTPUT_PARENT:/output" \
  wheelcam-app:demo python scripts/demo_chain.py \
  /orders/case-03/d20w10.5 --out /output/m59
```

这是 F1–F7 的单命令容器运行证据。网页工作台需在节点回环地址启动并通过 SSH 隧道访问。生成物仍全部是 `not_released` 工程草案；未验证真实机床、刀具/夹具碰撞、完整辐条造型 STEP 或制造批准。
