# PartPacker on GB10: first WheelCAM acceptance run

## Decision

The isolated GB10 path now has one valid compatibility stack. PyTorch 2.7 with
CUDA 12.8 produces a coherent three-part result for the official teapot control,
whereas PyTorch 2.11 with CUDA 13.1 deterministically produces 21 components on a
regular grid. The wheel run recovers the rim, five split/Y spoke groups, five lug
positions, openings and center in the front view, but several large layers remain
separated along depth. Keep PartPacker as an experimental WheelCAM candidate and
do not expose it as a released LocalPilot `image_to_mesh` capability yet.

The first wheel and teapot runs were invalid model-quality trials: the
deployment had `flow.pt` and DINOv2 but was missing the separately required
`vae.pt`. PartPacker logged that error and continued with a randomly initialized
340.30M-parameter VAE. The adapter now maps the official VAE to
`/weights/vae.pt`, and the runtime fails before inference when it is absent.

The input was valid. Both the pre-matted RGBA image and PartPacker's saved input
preview preserve the wheel, spokes, openings, rim, and center. The two raw
decoded volumes and the combined scene instead show dense cube-like periodic
artifacts. Those artifacts are evidence of the incomplete deployment, not a
quality verdict on the official PartPacker checkpoints.

An official-input control run reached the same failure. The unmodified upstream
`assets/images/teapot.png` was run with the same image, checkpoints, seed, 50
steps, default 384 grid and resource limits. Its input SHA-256 is
`967ec369e4bb45e835f7ac057d9406a46178ba6f2d6a5958788f805deb2fd5ec`.
Inference exited `0` without OOM, but the main and raw-volume depth views contain
the same cube-like periodic artifacts and no recognizable teapot. The control
made the missing VAE visible and rules out wheel-specific input preparation as
the explanation for those particular artifacts.

## Accepted stack control

The first controlled change only rolled Transformers 4.57.5 and tokenizers
0.22.2 back to 4.46.2 and 0.20.3. Every exported GLB remained byte-for-byte
identical to the PyTorch 2.11 failure, ruling out the DINO implementation version
as the cause.

The second controlled stack used the node's already-cached
`nvcr.io/nvidia/pytorch:25.02-py3` base:

- image: `sha256:acbdf2201c5871539768c019d7a39547cc382da52470349984781deff8d6a48e`
- PyTorch 2.7.0a0+nv25.02, CUDA 12.8, NumPy 1.26.4
- Transformers 4.46.2, tokenizers 0.20.3, torchvision 0.22.0a0
- official teapot run: `official-teapot-torch270-cu128-seed42-20260922T1028Z`
- runtime: `2026-09-22T10:29:06Z` to `10:41:57Z`; exit 0; OOM false
- sampled container-memory peak: 9.362 GiB
- main GLB: 24,067,684 bytes, SHA-256
  `7671107dacd5f77fb1e3bf618ac9593065131b2672f4c0a08a9421a019146177`
- output: one combined scene, two raw volumes and three substantial parts
- quality: pass for the official control; body, lid, spout, handle and base form
  one recognizable assembly without the regular component grid

The 50-step sampler took about 11 minutes 7 seconds, versus about 49 seconds on
the invalid PyTorch 2.11 stack. This older stack is useful for compatibility
proof but is not an acceptable performance target.

## Complete-weight wheel run

- run: `wheel-torch270-cu128-seed42-20260922T1045Z`
- runtime: `2026-09-22T10:46:40Z` to `11:00:42Z`; exit 0; OOM false
- sampled container-memory peak: 9.711 GiB
- main GLB: 44,664,772 bytes, SHA-256
  `97b468261fdfd684e895ac61eb886d5e38c7fae26fa8d680192a8f60a40a1a61`
- output: one combined scene, two raw volumes and nine parts
- front-view quality: recognizable rim, five split/Y spoke groups, five lug
  positions, narrow openings and center
- assembly quality: fail; side and oblique views show several large disc/ring
  layers separated along depth rather than one coherent wheel assembly

The result is useful model-quality evidence and a visible GB10 compatibility
milestone. It remains a visual mesh, not engineering CAD, and does not pass the
handoff gate for a formal LocalPilot capability.

## Step-count optimization

A one-variable A/B reduced `--num_steps` from 50 to 30 while holding the
accepted stack, weights, inputs, seed 42, 384 grid and resource limits fixed.
The official teapot still passed and end-to-end time fell from 12m 51s to
8m 21s. The wheel fell from 14m 02s to 9m 13s, but its combined scene increased
from 9 to 19 geometries and lost the five spoke groups, lug positions, openings
and center visible in the 50-step result. The 30-step setting is therefore
rejected as a wheel or global default; 50 steps remains the experimental wheel
baseline.

See [`OPTIMIZATION.md`](OPTIMIZATION.md) for the comparison table, hashes,
resource evidence, review paths and the rules for subsequent optimization.

## Reproducible inputs

- PartPacker source commit: `178d6c9408ecde857e6e716bab2763df6ce8bdc0`
- Complete-weight GB10 image ID:
  `sha256:69d2f9eae410d57092718bd5754816008a2f39cf615f2d2979150af6e2d82fdf`
- Flow checkpoint: 2,499,190,359 bytes, SHA-256
  `f73de9be11aa514fffe75cd8dece1c996060c2de916b3445004ae1aff4e19fcc`
- VAE checkpoint required by the flow config: 680,651,554 bytes, expected
  SHA-256 `bb2d3aa3b61dd902d73b938bb4ce90a842a78889e453b67b3b64dd641becad67`
- DINOv2 source: `facebook/dinov2-giant` at commit
  `611a9d42f2335e0f921f1e313ad3c1b7178d206d`
- DINOv2 weights: 4,546,005,432 bytes, SHA-256
  `917d3c470db999d32a312f8542149be91c7cbac61ee8fb4b67ae3d82b79ce21f`
- RGBA input SHA-256:
  `8f4a60f4e2cf523571d9d39f875c6f730eba817e89ad26bf40126cc4d5c23708`
- Parameters: one image, seed 42, 50 flow steps, default 384 grid,
  `--num_faces -1`

The files were downloaded directly on the assigned node. The HF mirror
redirected to the official Hugging Face Xet objects. Size and SHA-256 are
checked before a file is promoted from its partial-download name.

After the incomplete-weight runs, the image validator was strengthened to execute a
synthetic PyMCubes sphere test. The validator-only successor image is
`sha256:ada97dadaa6cea9521ba28382ab32768812595351a3ba116cd4abdf3fc0fdef4`.
The complete-weight image `69d2...` also includes the fail-closed VAE check and
the offline VAE path rewrite.

## Isolation and coexistence

Each acceptance run used a container with no network, a read-only root, read-only
source/input/weight mounts, a writable output mount, 12 CPUs, 512 PIDs, 32 GiB
memory and no additional swap. Existing `lp-vllm` and `node-exporter` containers
remained running before, during, and after the run. No driver, firewall, SSH,
firmware, or system package configuration was changed.

The run started at `2026-09-22T02:45:46Z` and ended at
`2026-09-22T02:51:46Z`. The 50-step flow sampler took about 50 seconds. VAE
decoding, marching cubes, conservative cleanup, splitting, and export accounted
for most of the remaining time.

The incomplete wheel run peaked at 30.42 GiB. The complete teapot control peaked
at 11.35 GiB. Both are observed Docker stats samples rather than direct GPU
allocator measurements. The first number reflects the randomly initialized VAE
failure and is not a valid capacity estimate for the official model.

## Output evidence

### Complete-weight official teapot control

- Run: `official-teapot-complete-seed42-20260922T0642Z`
- Runtime: `2026-09-22T06:47:11Z` to `2026-09-22T06:48:24Z`
- Inference exit: `0`; Docker `OOMKilled=false`
- Model-load evidence: `Loaded VAE from /weights/vae.pt`
- Sampled container-memory peak: 11.35 GiB
- Main GLB: 14,447,708 bytes, SHA-256
  `90b2ed43fd6ce92b37b28bc33e22c2582cc54b2f78a5666f18ac27144045a1f2`
- Raw volumes: 6,851,956 and 6,137,524 bytes
- Output GLBs: 24 total: one combined scene, two raw volumes, and 21 parts
- Quality result: fail; local body/spout/handle-like forms are recognizable,
  but the 21 components occupy repeated grid positions instead of forming one
  coherent teapot

Review
`results/official-teapot-complete-seed42-20260922T0642Z/main-contact-sheet.png`
with its `vol0` and `vol1` contact sheets. This is a valid full-checkpoint run,
so the remaining failure belongs to the GB10 compatibility path rather than a
missing model file.

### Incomplete-weight runs retained as diagnostics

- Inference exit: `0`; Docker `OOMKilled=false`
- Main GLB: 564,633,204 bytes, SHA-256
  `3f4a9bc0bf84eca7b42149973b814a5bdbf16b6146833922a8d3a6ab0083a099`
- Output GLBs: 214 total: one combined scene, two raw volumes, and 211 parts
- Combined scene: 211 geometries, 14,120,463 vertices, 28,212,230 triangles
- Watertight components: 159 of 211
- Raw volume sizes: 202,256,540 and 305,737,256 bytes

#### Incomplete-weight official teapot control

- Run: `official-teapot-seed42-20260922T1315Z`
- Runtime: `2026-09-22T04:34:09Z` to `2026-09-22T04:37:49Z`
- Inference exit: `0`; Docker `OOMKilled=false`
- Sampled container-memory peak: 22.64 GiB
- Main GLB: 428,239,952 bytes, SHA-256
  `66533496eba7eae5c797c731b6ee478f6fefdd451be4f48249641d905d1500cf`
- Output GLBs: 204 total, including 201 split parts
- Quality result: fail; no recognizable body, lid, spout, or handle
- Largest-part check: fail; the two largest components (parts 104 and 0,
  196,165,768 and 164,834,264 bytes) retain the same periodic cube/lattice
  structure when rendered separately

Review `results/official-teapot-seed42-20260922T1315Z/main-contact-sheet.png`
alongside its original `input.png`. The two raw-volume contact sheets and the
separate largest-part renders show that the output is not merely a valid subject
hidden by many small components. This run must not be used to judge official
PartPacker quality because `vae.pt` was absent.

A separate synthetic PyMCubes check generated a single watertight genus-zero
sphere with 1,248 vertices and 2,492 faces. Basic ARM64 Marching Cubes execution
therefore works, although that check does not prove numerical parity on the
model's real occupancy fields.

The untouched GLBs remain on the assigned node under
`~/wheelcam-lab/outputs/flow-seed42-20260922T0215Z/`. The local result directory
contains the input images, deterministic depth views, and copied evidence logs.
The interrupted local copy of the large GLB was discarded after the review
images were retrieved; the complete, hashed original remains on the node.

## Quality gates

The table evaluates the complete-weight PyTorch 2.7 wheel run against the actual
five-lug reference image. The earlier six-group/six-lug wording was incorrect.

| Gate | Result | Evidence |
| --- | --- | --- |
| Five split/Y spoke groups | Pass | Front depth view preserves five paired groups |
| Five lug positions | Pass | Five positions are visible around the center |
| Open narrow windows | Pass | Major spoke and outer-rim openings remain open |
| Plausible rim silhouette | Pass | Front outline and barrel depth are wheel-like |
| Recognizable center | Pass | Hub and center cap region are visible |
| Coherent assembled depth | Fail | Large disc/ring layers are separated in side and oblique views |

Review
`results/wheel-torch270-cu128-seed42-20260922T1045Z/main-contact-sheet.png`
with both raw-volume contact sheets. The failed assembly gate blocks formal
`image_to_mesh` handoff even though the front-view topology is useful.

## Failed attempts retained

1. The first runtime attempt exited before model loading because the upstream
   script path did not expose the repository root to Python. The adapter now
   sets `PYTHONPATH` explicitly.
2. The second attempt proved that `flow.pt` has an undeclared runtime dependency
   on `facebook/dinov2-giant`. Network isolation correctly blocked an implicit
   download. The adapter now uses a pinned, read-only local copy and forces
   Transformers and Hugging Face offline mode.
3. The third attempt completed and produced the evidence above.
4. The first completed wheel and teapot exports were invalid because `vae.pt`
   was absent; the adapter now rewrites that path and fails closed.
5. A diagnostic occupancy-field run captured the random VAE's whole-volume sign
   oscillation, then reached the 32 GiB container limit after the required
   evidence was written. It is retained as failure evidence, not a benchmark.

## Next experiment

Do not add PartPacker to LocalPilot yet. The 30-step speed candidate has been
rejected for loss of wheel topology. The next bounded quality experiment should
test whether the 50-step wheel's depth-separated layers are seed instability or
a systematic single-view ambiguity: run two additional seeds with the accepted
stack and the same input, then compare only the assembly-depth gate. Stop PartPacker if both
repeat the separation. If either forms a coherent wheel, measure repeatability
on a small public wheel set before defining a draft `image_to_mesh` task. In
parallel, evaluate SAM3D or TRELLIS as the fallback route rather than tuning the
failed PyTorch 2.11 stack.
