# PartPacker GB10 compatibility experiment

This is an experimental WheelCAM adapter, not a released LocalPilot capability.
It preserves the existing vLLM container and never exposes a network service.

The upstream code imports `pymeshlab` through `kiui.mesh_utils`. The pinned
PyMeshLab release has no compatible Linux ARM64 wheel for this target. For the
first inference only, this adapter replaces the called cleanup operations with
a conservative NumPy/Trimesh implementation. It removes invalid/duplicate faces,
unreferenced vertices and components below upstream's thresholds. It does not
repair, remesh or decimate. `--num_faces -1` is mandatory.

The original candidate inherited NVIDIA's 26.02 vLLM image with PyTorch 2.11,
CUDA 13.1 and NumPy 2.1. It executed on GB10, but the official teapot control
decoded into 21 regularly repeated components. A controlled rollback to the
already-cached `nvcr.io/nvidia/pytorch:25.02-py3` base fixed that numerical
failure. The accepted compatibility stack is PyTorch 2.7.0a0, CUDA 12.8, NumPy
1.26.4, Transformers 4.46.2 and torchvision 0.22.0a0. It is much closer to
upstream's tested PyTorch 2.5.1/CUDA 12.1 environment while retaining GB10
support. PyMCubes is compiled in the container with build isolation disabled.

The runtime defaults to `/inputs/wheel-reference-rgba.png`. It must already have
an alpha channel. The experiment excludes `rembg` and ONNX Runtime and rejects
RGB input, so inference cannot download a background-removal model. Set
`PARTPACKER_INPUT` only when intentionally testing another single mounted RGBA
image.

Upstream pins `kiui==0.2.15`, but that release is absent from the package index
used by the GB10 build. This image uses the adjacent `0.2.16` release and checks
the three APIs used by flow inference during the image build. This is another
compatibility deviation that the real inference gate must validate.
The normal package install is retained so kiui's required `lazy-loader`,
`varname` and `objprint` dependencies are present; its optional `full` extra is
not installed because it would reintroduce PyMeshLab and unrelated GUI tools.

Before building, place the official CPython 3.12 Linux ARM64 Cython 3.0.12
wheel in `wheels/`; its required SHA-256 is
`a7fec4f052b8fe173fe70eae75091389955b9a23d5cec3d576d21c5913b49d47`.
Also place `kiui-0.2.16-py3-none-any.whl` with SHA-256
`a33453b76a689cbc8be3fcf8f0cb6e16b4c9b3c78956e5e0bf6270077ccfa2cf`.
These wheels are not committed. The legacy PyTorch 2.11 Dockerfile additionally
expects the SciPy wheel documented in that file; the accepted PyTorch 2.7 base
already contains NumPy 1.26.4 and SciPy 1.12.0.

Build the accepted GB10 compatibility candidate from this directory. The base
image must already be present on the assigned node; `--pull=false` avoids an
implicit large download.

```bash
docker build --pull=false -f Dockerfile.torch270-cu128 \
  -t wheelcam-partpacker:gb10-torch270-cu128 .
```

`Dockerfile` retains the faster PyTorch 2.11/CUDA 13.1 failure candidate for
diagnosis. Do not use it for accepted geometry.

Run only after weights and the public test image are available on the node:

The flow checkpoint excludes both the VAE and DINOv2 weights. Download the
official PartPacker `vae.pt` separately and place it at `pretrained/vae.pt`.
Its expected size is `680651554` bytes and SHA-256 is
`bb2d3aa3b61dd902d73b938bb4ce90a842a78889e453b67b3b64dd641becad67`.
The adapter maps the upstream relative VAE path to the read-only `/weights`
mount and fails before inference when that file is absent.

The flow checkpoint also loads the public `facebook/dinov2-giant` encoder. Pin
that repository to commit `611a9d42f2335e0f921f1e313ad3c1b7178d206d` and place
its `config.json` plus `model.safetensors` under
`pretrained/dinov2-giant/`. The expected `model.safetensors` size is
`4546005432` bytes and its SHA-256 is
`917d3c470db999d32a312f8542149be91c7cbac61ee8fb4b67ae3d82b79ce21f`.
The adapter rewrites the upstream model reference to this local directory and
forces Hugging Face and Transformers offline mode during inference.

```bash
docker run --rm --gpus all --network none --read-only \
  --tmpfs /tmp:rw,size=16g --ipc=private --shm-size=2g \
  --memory=32g --memory-swap=32g --pids-limit=512 --cpus=12 \
  --mount type=bind,src="$PWD/PartPacker",dst=/opt/partpacker-source,readonly \
  --mount type=bind,src="$PWD/inputs",dst=/inputs,readonly \
  --mount type=bind,src="$PWD/pretrained",dst=/weights,readonly \
  --mount type=bind,src="$PWD/outputs",dst=/outputs \
  wheelcam-partpacker:gb10-torch270-cu128 --limit 1 --num_steps 50 --seed 42
```

Before inference, record source, image and weight SHA-256 values, free disk and
the running containers. Run in tmux. After inference, retain the unmodified GLB,
logs, elapsed time and observed memory source. Do not claim measured peak memory
without a sampler or engine source.

Acceptance belongs to WheelCAM. For the current reference, check five split/Y
spoke groups, five lug positions, open narrow windows, plausible rim silhouette,
recognizable center structure and coherent depth alignment of all parts.
The generated mesh remains a visual reference, not CAD, dimensional evidence or
a manufacturing model. A failed gate is a valid result and must not be packaged
as `image_to_mesh` support.

The first GB10 run and official teapot control exported GLBs but were invalid
quality trials because `vae.pt` was absent and upstream continued with a random
VAE. The complete-weight PyTorch 2.11 control still places 21 recognizable components
on a regular grid. The PyTorch 2.7/CUDA 12.8 control instead produces a coherent
three-part teapot, proving the compatibility failure is stack-sensitive. The
wheel run recovers the front-view topology but leaves several layers separated
along depth, so it remains experimental and is not a released capability. A
controlled 50-to-30-step trial reduced end-to-end runtime by about 34%, but the
wheel lost its spokes, lug positions, openings and center; 30 steps is therefore
rejected as a default. See [`RESULTS.md`](RESULTS.md) for compatibility evidence
and [`OPTIMIZATION.md`](OPTIMIZATION.md) for the speed/quality A/B, hashes and
next optimization rules.
