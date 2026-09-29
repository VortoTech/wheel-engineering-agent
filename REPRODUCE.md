# 复现指南（给评委）

在自己的电脑上从零复现 WheelCAM，**不需要我们的私有订单、Spark 或 GPU**。
2026-09-29 在两台机器上按本文命令从全新克隆、全新虚拟环境实测：用户目录为空（无私有敏感词清单）、不设任何模型环境变量。

## 最低运行配置

| 项目 | 要求 | 实测依据 |
|---|---|---|
| 操作系统 | macOS 或 Linux（Ubuntu 24.04 已测） | macOS 26 arm64；Ubuntu 24.04 aarch64（DGX Spark）；GitHub Actions ubuntu-24.04 x86_64 跑核心回归 |
| CPU 架构 | arm64 或 x86_64 | 同上；Windows 未测试 |
| Python | 3.12 | `pyproject.toml` 允许 3.12–3.13，只测过 3.12 |
| 内存 | 最低 4 GB，建议 8 GB | 快速检查峰值 1.3 GB；示例全链峰值 1.1 GB；全量测试峰值 3.5 GB |
| 磁盘 | 约 4 GB 空闲 | 虚拟环境 1.3–1.7 GB，另有同等大小的下载缓存 |
| GPU | 不需要 | 建模、STEP、工程图、加工包都在 CPU 上运行；只有可选的模型档需要一个模型服务 |
| 网络 | 首次安装需访问 PyPI 或镜像 | 依赖锁定并校验哈希，换镜像不改变安装结果 |
| Linux 系统库 | `libgl1 libglib2.0-0 libxrender1 libsm6` | OCC/CadQuery 导出 STEP 需要，无需显示器 |
| 可选 | Chrome/Chromium | 有则工程图额外输出 A3 PDF，没有则只出 SVG |

## 第一档：无模型、无私有数据（任何电脑）

### 1. 安装（二选一）

```bash
git clone https://github.com/VortoTech/wheel-engineering-agent.git
cd wheel-engineering-agent

# 方式 A：uv（推荐）
uv sync --locked --extra test

# 方式 B：只用系统 Python 和 pip
python3.12 -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-runtime.txt
.venv/bin/pip install pytest
```

Linux 先安装系统库：`sudo apt-get install -y libgl1 libglib2.0-0 libxrender1 libsm6`。
国内网络访问 PyPI 慢时加 `--index-url https://mirrors.cloud.tencent.com/pypi/simple`（方式 B）或设置 `UV_INDEX_URL`（方式 A）。
**注意**：部分镜像同步滞后，缺少锁定版本（2026-09-29 实测阿里云镜像缺 `manifold3d==3.5.4`），换腾讯云、华为云镜像或官方 PyPI 即可。

| 实测 | Mac（uv） | Spark（pip + 腾讯云镜像） |
|---|---:|---:|
| 安装耗时 | 31 s | 153 s |
| 虚拟环境大小 | 1.3 GB | 1.7 GB |

### 2. 快速检查（约 2–5 分钟）

与 GitHub Actions 核心回归相同的测试集，加上公开示例订单的加工包测试：

```bash
.venv/bin/python -m pytest -q \
  tests/test_privacy.py tests/test_skill_package.py tests/test_localpilot_skill.py \
  tests/test_workbench.py tests/test_workbench_revision.py tests/test_text_wheel.py \
  tests/test_wheel_skill.py tests/test_machining_step.py tests/test_manufacturing_step_demo.py
```

预期：**72 passed, 4 skipped**。跳过的 4 项需要私有订单或本机模型，缺少时自动跳过。
实测：Spark 1 分 31 秒，Mac 4 分 38 秒。

### 3. 公开示例订单全链路（约 30–60 秒）

`examples/sample-order/` 是用仓库里的参数预设生成的订单：正面图 + 确认单规格（20×9 ET35，5×114.3，中心孔 73.1，孔型 14X28X60）。
不含任何客户数据；`sample_truth.json` 是生成它的配方，可作为已知答案。可以用 `scripts/make_sample_case.py` 重新生成。

```bash
PYTHONPATH=services .venv/bin/python scripts/demo_chain.py examples/sample-order --out runs/sample
```

预期输出（最后一行）：`chain: 4/4 steps ok`。四步依次是：

| 步骤 | 产物（在 `runs/sample/` 下） | 实测 Spark / Mac |
|---|---|---:|
| 重建：照片 + 规格 → 配方与网格，识别出 5 辐 | `reconstruct/recipe.json`、`reconstruct/cad/wheel.glb`、`reconstruct/engineering_report.json` | 2.6 s / 5.4 s |
| 加工级 STEP：毛坯 + 零件，单实体、STEP 回读、无碎面、ET 检查 | `machining/stock.step`、`machining/machining.step`、`machining/machining_report.json` | 19.2 s / 24.9 s |
| 加工包：工序单、刀具表、参考 NC、采样去料仿真（无过切） | `package/process_plan.json`、`package/tool_list.csv`、`package/reference.nc`、`package/simulation_3d.png` | 3.5 s / 6.7 s |
| 工程图 | `drawing.svg`（有 Chrome/Chromium 时另有 `drawing.pdf`） | 0.5 s / 16.0 s（含 PDF） |
| **合计** | `chain.json`、`delivery_manifest.json`（全部产物的 SHA-256） | **26 s / 53 s** |

锥座深度会被自动上提并记录在 `adjustments`：示例孔型在该 ET 下直孔余量不足，这是预期的工程调整，报告要求工程师确认。

### 4. 全量测试（约 1 小时，可选）

```bash
.venv/bin/python -m pytest -q
```

Mac 实测：**624 passed, 5 skipped，0 failed，53 分钟，内存峰值 3.5 GB**。跳过项均依赖私有数据。

### 5. 打开工作台

```bash
PYTHONPATH=services .venv/bin/python -m wheelcam.workbench --runs runs/demo --port 8795
```

浏览器打开 <http://127.0.0.1:8795>，“用图片新建”选 `examples/sample-order/front.jpg`，填入上面的规格。
不配模型时不要勾选“用视觉模型核对并修正造型”（工作台会提示未配置）；文字新建和对话修改需要第二档。

## 第二档：接一个 OpenAI 兼容的模型

文字新建、对话修改和造型 Agent 需要一个支持图片输入的 OpenAI 兼容接口（vLLM、Ollama 等均可）。

```bash
export WHEELCAM_CHAT_BASE_URL=http://127.0.0.1:8000/v1  WHEELCAM_CHAT_MODEL=<模型名>
export WHEELCAM_VLM_BASE_URL=http://127.0.0.1:8000/v1   WHEELCAM_VLM_MODEL=<模型名>

PYTHONPATH=services .venv/bin/python scripts/demo_chain.py \
  --text "做一个 20 寸 5 辐直辐轮毂，20×9 ET35，5×114.3，中心孔 73.1。" --out runs/text
PYTHONPATH=services .venv/bin/python scripts/demo_chain.py examples/sample-order --out runs/sample-agent
```

我们实测的模型是 `step3-vl-10b-fp8`（vLLM，DGX Spark）。换用其他模型时，JSON 输出格式和“是/否”判断的稳定性需要自行确认；审核层会拒绝不合规的提案，不会让模型改工程尺寸。

2026-09-29 在 Spark 上用同一个全新虚拟环境实测（无私有造型库）：

| 运行 | 结果 | 耗时 / 内存峰值 |
|---|---|---:|
| 文字新建 → 全链路 | 4/4 步通过；6 项规格均来自原文；孔型未给，按模板并追问 | 23 s / 0.97 GB |
| 示例订单 + 造型 Agent | 4/4 步通过；模型判断“无盲窗”（p_true 0.01，正确）；窗口斜面 16/22 → 8/11 mm | 64 s / 1.2 GB |

示例订单有已知答案（`sample_truth.json`：斜面 4/6 mm，无盲窗）。Agent 的斜面比预设更接近答案，但**没有选中 4/6**：
在这张合成图上 8/11 与 4/6 的照片边缘距离只差 0.16 mm（2.66 vs 2.82 mm），边缘比对区分不开。这是造型 Agent 的已知局限。

**没有私有造型库时**，文字新建使用仓库内的公开参数模板，造型比演示视频里的简单；演示视频中的造型库来自真实订单，属私有数据，不随仓库分发。

## 第三档：DGX Spark / 容器

`Dockerfile.spark` 基于 `python:3.12-slim`，按 `requirements-runtime.txt` 校验哈希安装。Spark 上的部署、模型服务与全链结果见 [Spark 全链证据](docs/spark-full-chain-evidence.md)。

## 哪些不能由评委复现

| 内容 | 原因 | 我们提供的替代 |
|---|---|---|
| 13 个真实订单的重建与工厂 CAD 对照误差 | 客户订单与工厂 CAD 不能公开 | 汇总结果（`docs/prd-final-demo.md` §5）与下方留一法；代码与评测脚本（`experiments/real-orders/`）公开 |
| 演示视频里的文字造型库 | 轮廓来自客户订单 | 生成脚本 `wheelcam.text_style_library` 公开；无库时自动回退公开模板 |

### 泛化误差：留一法（2026-09-29）

轮缘模板常数由同一批 13 个 CAD 标定。为估计新订单的误差，每次拿掉 1 个型号、用其余 12 个标定、再预测被拿掉的那个：

| 量 | 同批标定误差 | 留一法误差（新订单预期） |
|---|---|---|
| 外径 | 0 mm | 0 mm（13 个型号的单侧轮缘高度都是 19.9 mm） |
| 总宽 | 平均 1.76 mm，最大 4.88 mm | 平均 2.03 mm，中位 1.36 mm，最大 5.38 mm |

轮辋截面误差（中位 1.98 mm）尚未做留一法，仍是同批残差。ET、PCD、孔数、中心孔来自确认单，不依赖标定。
