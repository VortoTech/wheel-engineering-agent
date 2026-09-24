#!/usr/bin/env bash
set -euo pipefail

test -d /opt/partpacker-source/.git
test -d /inputs
test -d /outputs
test -d /weights
test -f /weights/vae.pt
test -f /weights/dinov2-giant/config.json
test -f /weights/dinov2-giant/model.safetensors
input_path="${PARTPACKER_INPUT:-/inputs/wheel-reference-rgba.png}"
test -f "$input_path"

work=/tmp/PartPacker
cp -a /opt/partpacker-source "$work"
python3 /opt/wheelcam/apply_compat.py "$work"
cd "$work"

python3 - <<'PY'
import platform, torch
assert platform.machine() == "aarch64"
assert torch.cuda.is_available()
print("runtime", platform.machine(), torch.__version__, torch.version.cuda, torch.cuda.get_device_name(0))
PY

exec env \
  PYTHONPATH="$work${PYTHONPATH:+:$PYTHONPATH}" \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  python3 flow/scripts/infer.py \
  --ckpt_path /weights/flow.pt \
  --input "$input_path" \
  --output_dir /outputs \
  --num_faces -1 \
  "$@"
