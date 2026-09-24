# GB10 图像转三维候选

更新日期 2026-09-21。目标是使用队伍分配的 NVIDIA GB10，对 WheelCAM 当前商品图进行实机模型候选测试。
本页区分官方能力、目标机兼容性和实际运行结果；未跑通的候选不记为已部署。

## 当前优先级

### 1 NVIDIA PartPacker

- 来源：NVIDIA Research 官方 `NVlabs/PartPacker`。
- 输入输出：单张 RGB 图像生成可分件编辑的三角网格，输出 GLB；官方模型卡说明输入会缩放到 518×518，输出不含纹理。
- 资源：官方 README 称 FP16 推理约 10 GB GPU 内存；权重仓库约 3.18 GB，其中 flow 约 2.5 GB、VAE 约 681 MB。
- 许可证：NVIDIA Non-Commercial License，只能用于非商业研究。本次黑客松实验可作为候选验证，不能据此进入商业发布。
- 官方验证边界：代码说明测试过 Torch 2.5.1、CUDA 12.1 及 3090/4090；模型卡列出 Ampere/Hopper，未列 Blackwell 或 GB10。
- WheelCAM 价值：分件几何可能比整体外观网格更适合分析轮辋、辐条和中心盘，但模型没有轮毂工程约束。
- 当前状态：正在队伍 GB10 的 `tmux` 会话 `wheelcam-partpacker` 中克隆官方源码到
  `~/wheelcam-lab/PartPacker`；尚未安装依赖、下载权重或执行推理。

验收时至少记录：源码提交、依赖版本、权重摘要、输入图摘要、耗时、峰值内存、GLB 摘要、
正面投影轮廓、六组周期一致性、孔数量/连通性和人工灰模观察。模型输出不能直接替代当前工程曲面。

### 2 NVIDIA Isaac Video to Data 的 SAM3D 模块

- 来源：NVIDIA Isaac 官方 `nvidia-isaac/video_to_data` 集成模块。
- 能力：`v2d_sam3d` 接收图像与 mask，输出 GLB、位姿和相机内参，并可渲染回投影调试图。
- 底层：SAM 3D Objects 来自 Meta，并非 NVIDIA 自研权重；权重需要 Hugging Face gated access 和相应许可证同意。
- 兼容性：Video to Data 当前对部分 Blackwell/TensorRT/cuVSLAM 路线有明确不支持说明；单独 SAM3D 模块是否能在 GB10
  工作仍须实测，不能由整个仓库支持 ARM64 推断。
- 当前状态：未安装、未下载权重。若 PartPacker 失败，再做单模块预检，不直接部署整套视频重建流水线。

### 3 NVIDIA 3D Object Generation Blueprint

- NVIDIA 官方 Blueprint 负责端到端编排，但图像转三维主体使用 Microsoft TRELLIS。
- 可作为 LocalPilot 后续“多引擎编排”设计参考，不属于 NVIDIA 自研图像转三维模型。
- 目前不作为“NVIDIA 模型优先”的第一候选。

### 不优先

- NVIDIA 3D Object Reconstruction Framework：面向双目/多视图序列、标定、位姿和神经表面重建；只有单张商品图时输入证据不足。
- GET3D：直接生成带纹理网格，但主要是类别生成模型，不是从这张商品图进行实例重建；依赖栈也较旧。
- Asset Harvester 与 InstantNuRec：面向自动驾驶多相机日志和 3D Gaussian 场景，不符合单张轮毂商品图输入。

## LocalPilot 接入边界

GB10 已有 `~/localpilot-current`，LocalPilot 0.2.0 已在真实目标上运行 `doctor` 与 `engines`：
识别为 DGX Spark、119.7 GB 统一内存、20 核 ARM64，并发现 `127.0.0.1:8000` 的真实 vLLM 服务。
这不是新部署；服务当前模型为 Step3-VL-10B-FP8。

LocalPilot 当前引擎契约主要面向 OpenAI 兼容的文本/视觉服务，`vision` 代表图像理解，不代表图像转三维。
PartPacker 是离线批处理引擎，不能伪装成 vLLM 模型加入现有验收。合理扩展是新增独立 `image_to_mesh` 任务与批处理运行器，
记录文件输入、GLB 输出、运行时间、峰值内存和几何质量门槛；在实现前由 WheelCAM 直接保存实验报告。

## 现有负载保护

GB10 上的 `lp-vllm` 容器正在运行，检查时进程约占 58 GiB，共享内存约 54 GiB 可用。
PartPacker 官方推理估计约 10 GB，但统一内存和现有服务会共享容量，正式运行前必须再次测量。
不停止、重启或替换现有 vLLM；若兼容性测试因内存失败，保存失败证据并等待用户决定是否安排维护窗口。

节点使用继续遵守 `gb10-preflight.md` 中从组委会手册提取的限制：大权重在节点直接下载、长任务使用 tmux、
公共模型目录只读、磁盘保留至少 20%、不修改驱动/网络/系统服务、不开放无鉴权公网端口。
