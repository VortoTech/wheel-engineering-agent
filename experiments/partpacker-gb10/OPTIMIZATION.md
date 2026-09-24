# PartPacker GB10 optimization record

## Scope and decision

This record covers one controlled optimization on the accepted PartPacker GB10
compatibility stack: reducing flow-sampling steps from 50 to 30. Every other
known input was held constant: source commit, container image, checkpoints,
input image, seed 42, default 384 grid, mesh cleanup, resource limits and
network isolation.

The optimization is **rejected as a general or wheel default**. It reduced end
to end runtime by about 34%, and the official teapot control still passed, but
the WheelCAM reference lost its defining spoke, lug and center structure. Keep
50 steps as the current experimental baseline. The 50-step wheel itself still
fails coherent assembly depth, so neither result is ready for a released
`image_to_mesh` capability.

## Fixed environment

- target: assigned NVIDIA GB10 node, Linux ARM64
- PartPacker commit: `178d6c9408ecde857e6e716bab2763df6ce8bdc0`
- image: `wheelcam-partpacker:gb10-torch270-cu128`
- image ID: `sha256:acbdf2201c5871539768c019d7a39547cc382da52470349984781deff8d6a48e`
- runtime: PyTorch 2.7.0a0+nv25.02, CUDA 12.8, NumPy 1.26.4,
  Transformers 4.46.2, tokenizers 0.20.3
- resources: 12 CPUs, 32 GiB container memory, no swap beyond that limit,
  512 PIDs, read-only root, read-only source/input/weight mounts, no network
- parameters held constant: seed 42, default 384 grid, `--num_faces -1`
- changed axis: `--num_steps 50` to `--num_steps 30`

Existing `lp-vllm` and `node-exporter` containers remained running. No driver,
firewall, SSH, firmware or system package setting was changed.

## Results

| Input | Steps | Sampling | End to end | Observed container-memory peak | Parts | Quality gate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Official teapot | 50 | 11m 07s | 12m 51s | 9.362 GiB | 3 substantial | Pass |
| Official teapot | 30 | 6m 39s | 8m 21s | 9.380 GiB | 3 substantial | Pass |
| WheelCAM wheel | 50 | 11m 12s | 14m 02s | 9.711 GiB | 9 | Front topology pass; assembly-depth fail |
| WheelCAM wheel | 30 | 6m 38s | 9m 13s | 9.347 GiB | 19 | Fail; defining front topology lost |

For the teapot, 30 steps reduced sampling time by 40.2% and end-to-end time by
35.0%. For the wheel, it reduced sampling time by 40.8% and end-to-end time by
34.3%. Peak memory did not materially change, so step reduction is a latency
control rather than a capacity optimization.

The official teapot remains recognizable at 30 steps: body, lid, spout, handle
and base form the same three-part assembly seen at 50 steps. This proves the
30-step path can work for a comparatively forgiving object, but it does not
establish a safe global default.

The wheel result fails the task-specific gate. Its combined GLB contains 19
geometries instead of 9. The front projection is dominated by the outer rim and
barrel; the five split/Y spoke groups, five lug positions, narrow openings and
recognizable center present in the 50-step result are not retained. The raw
volumes also remain separated as barrel/ring layers. The runtime gain therefore
cannot be accepted for WheelCAM.

## Evidence

### Official teapot, 30 steps

- run: `official-teapot-torch270-cu128-steps30-seed42-20260922T1115Z`
- runtime: `2026-09-22T11:19:47Z` to `11:28:08Z`
- exit: `0`; Docker `OOMKilled=false`
- main GLB: 24,159,016 bytes
- main SHA-256: `d4e3ec4fe15c72f2fbf1d883e0e1cf9c4ff3aa0e45d2524fbdf892a7dda94a05`
- remote output: `~/wheelcam-lab/outputs/official-teapot-torch270-cu128-steps30-seed42-20260922T1115Z/`
- local review: `results/official-teapot-torch270-cu128-steps30-seed42-20260922T1115Z/`

### WheelCAM wheel, 30 steps

- run: `wheel-torch270-cu128-steps30-seed42-20260922T1135Z`
- runtime: `2026-09-22T11:37:00Z` to `11:46:13Z`
- exit: `0`; Docker `OOMKilled=false`
- main GLB: 54,741,408 bytes
- main SHA-256: `ab3ae47ae8901a98a10d86b4f31d3e3d5000e576f0e43207407599e648b6bf56`
- remote output: `~/wheelcam-lab/outputs/wheel-torch270-cu128-steps30-seed42-20260922T1135Z/`
- local review: `results/wheel-torch270-cu128-steps30-seed42-20260922T1135Z/`

Memory values are observed `docker stats` samples from
`resource-samples.csv`. They are not PyTorch allocator peaks and must not be
presented as direct GPU-memory measurements.

## Optimization rules derived from this test

1. Do not promote a speed setting from a generic control alone. It must pass the
   target task's semantic gates on the same deliverable.
2. Keep grid resolution at 384 until spoke, opening and center retention is
   stable. Lowering it directly risks the details that already disappeared at
   30 steps.
3. Keep 50 steps as the wheel baseline while testing optimizations that should
   preserve solver semantics, such as attention-kernel selection.
4. Any attention or runtime-stack change must first pass the official teapot,
   then the wheel topology and assembly-depth gates, using a one-variable A/B.
5. Run the two planned additional 50-step wheel seeds before treating the depth
   separation as tunable. If both repeat it, stop PartPacker for this task and
   compare SAM3D or TRELLIS instead.

## Product boundary

This work is a Skill-guided deployment and acceptance experiment. LocalPilot
did not automatically install or execute PartPacker. The outcome demonstrates a
reusable optimization method—fixed evidence, one changed axis, measured runtime
and task-specific quality rejection—but it does not add a released LocalPilot
capability. All meshes remain visual references, not CAD, dimensional evidence
or manufacturing models.
