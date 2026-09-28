#!/usr/bin/env bash
# Rebuild the evidence-based Chinese-voice demo (no narration subtitles).
set -euo pipefail
cd "$(dirname "$0")/../demo-video"
python3 scripts/prepare.py
python3 scripts/voiceover.py --voice "${DEMO_VOICE:-Tingting}" --rate "${DEMO_VOICE_RATE:-200}"
if [[ ! -d node_modules ]]; then npm ci; fi
npx tsc --noEmit
mkdir -p ../runs/demo-video
npx remotion render src/index.ts WheelDemo ../runs/demo-video/wheelcam-demo-zh-1080p.mp4 \
  --codec=h264 --crf=18 --concurrency=2 "$@"
echo 'Video: runs/demo-video/wheelcam-demo-zh-1080p.mp4'
