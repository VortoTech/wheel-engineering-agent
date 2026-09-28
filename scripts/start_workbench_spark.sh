#!/usr/bin/env bash
# Local workbench + the existing Spark model. Does not deploy or restart Spark services.
set -euo pipefail
cd "$(dirname "$0")/.."
run_root="${1:-runs/workbench-refinement-20260928}"
web_port="${2:-8795}"
model_port="${3:-18096}"
[[ "$web_port" =~ ^[0-9]+$ && "$model_port" =~ ^[0-9]+$ ]] || { echo 'Ports must be numbers' >&2; exit 2; }
ssh -N -o BatchMode=yes -o ForkAfterAuthentication=no -o ControlMaster=no -o ControlPath=none \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 \
  -L "127.0.0.1:${model_port}:127.0.0.1:8000" spark &
tunnel_pid=$!
trap 'kill "$tunnel_pid" 2>/dev/null || true' EXIT
ready=false
for attempt in {1..15}; do
  kill -0 "$tunnel_pid" 2>/dev/null || { echo 'Spark tunnel failed' >&2; exit 1; }
  if curl --noproxy 127.0.0.1 -fsS --max-time 2 "http://127.0.0.1:${model_port}/v1/models" >/dev/null; then ready=true; break; fi
  sleep 1
done
[[ "$ready" == true ]] || { echo 'Spark model is unavailable' >&2; exit 1; }
echo "Workbench: http://127.0.0.1:${web_port} (model on Spark, CAD on this Mac)"
NO_PROXY="127.0.0.1,localhost,${NO_PROXY:-}" \
no_proxy="127.0.0.1,localhost,${no_proxy:-}" \
WHEELCAM_CHAT_BASE_URL="http://127.0.0.1:${model_port}/v1" \
WHEELCAM_CHAT_MODEL=step3-vl-10b-fp8 \
WHEELCAM_VLM_BASE_URL="http://127.0.0.1:${model_port}/v1" \
WHEELCAM_VLM_MODEL=step3-vl-10b-fp8 PYTHONPATH=services \
  .venv/bin/python -m wheelcam.workbench --runs "$run_root" --port "$web_port"
