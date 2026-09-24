# Stable Fast 3D 视觉重建

WheelCAM 可把主参考图交给独立安装的 Stable Fast 3D，保存带纹理的 GLB 并在“AI 视觉重建”页查看。该结果只用于造型对照：不是参数化 CAD、不含可靠尺寸或背面结构、不生成 STEP，也不进入几何、强度、毛坯、CAM 或 NC 检查。

## 为什么使用独立环境

Stable Fast 3D 使用自己的 PyTorch/CUDA/MPS 依赖。WheelCAM 只通过受控命令行调用它，避免与 CadQuery 环境混装。模型仓库还需要在 Hugging Face 接受许可并获得权重访问权限。

```bash
git clone https://github.com/Stability-AI/stable-fast-3d.git /path/to/stable-fast-3d
cd /path/to/stable-fast-3d
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/huggingface-cli login
```

根据官方仓库当前说明完成其余平台依赖。若安装在仓库内的 `.local/stable-fast-3d`（含 `run.py` 与 `.venv/bin/python`），未设置下列变量时会自动使用该位置；显式设置的变量始终优先。安装在其他位置时，在启动 WheelCAM 前配置：

```bash
export WHEELCAM_SF3D_ROOT=/path/to/stable-fast-3d
export WHEELCAM_SF3D_PYTHON=/path/to/stable-fast-3d/.venv/bin/python
export WHEELCAM_SF3D_DEVICE=mps
PYTHONPATH=services uv run uvicorn wheelcam.app:app --host 127.0.0.1 --port 18765
```

可选配置：`WHEELCAM_SF3D_MODEL`（默认 `stabilityai/stable-fast-3d`）、`WHEELCAM_SF3D_TEXTURE_RESOLUTION`（默认 1024）及 `WHEELCAM_SF3D_TIMEOUT`（默认 1800 秒）。服务会自动设置 `PYTORCH_ENABLE_MPS_FALLBACK=1`。

对于白底产品图，WheelCAM 会在送入模型前把外部白底和轮辐孔洞转换为透明通道，避免通用抠图把孔洞封成实心圆盘。非白底图片仍交给 Stable Fast 3D 自带的背景移除流程。Numba 编译缓存保存在工作区共享缓存目录，后续任务可复用。

商用前需自行核对 Stability AI Community License 的资格、注册和归属要求；WheelCAM 的状态页只提示这一边界，不代替法律判断。
