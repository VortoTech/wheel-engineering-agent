# Installation and runtime setup

## Skill installation

Copy the complete `skills/wheel-engineering/` directory into your assistant's skill directory, keeping scripts and references together. Typical destinations are `~/.codex/skills/wheel-engineering` or `~/.claude/skills/wheel-engineering`. Do not copy only SKILL.md. Existing installations should be backed up or reviewed before replacement.

For Codex's skill-installer, provide repository `VortoTech/wheel-engineering-agent` and path `skills/wheel-engineering`. The repository is private: only authorized users can fetch it. No public license is currently granted; this package does not change repository access or redistribution rights.

## Runtime installation

Clone the authorized repository separately, then from that checkout run:

```bash
uv sync --extra test
```

Requires Python 3.12 or 3.13 and the dependencies locked by the runtime. Keep the skill and runtime from the same reviewed commit when possible. This first package exposes CLI compatibility, not a separately versioned stable service API.

Configure absolute paths in the caller's environment:

```bash
export WHEEL_ENGINEERING_RUNTIME=/absolute/path/to/wheel-engineering-agent
# Optional override; otherwise use the runtime checkout's .venv Python
export WHEEL_ENGINEERING_PYTHON=/absolute/path/to/wheel-engineering-agent/.venv/bin/python
python3 /absolute/path/to/installed/wheel-engineering/scripts/run.py doctor
```

The launcher uses only Python's standard library. The selected runtime Python must have CAD and geometry dependencies installed. Doctor imports runtime modules; it does not verify model connectivity, geometric quality, or Spark deployment. The launcher does not read .env files automatically.

## Models

Text and chat use `WHEELCAM_CHAT_BASE_URL`, `WHEELCAM_CHAT_MODEL` and, if needed, `WHEELCAM_CHAT_API_KEY`. Style-agent vision uses `WHEELCAM_VLM_BASE_URL`, `WHEELCAM_VLM_MODEL`, `WHEELCAM_VLM_API_KEY`, with runtime-defined chat fallbacks. Legacy `--use-vlm` uses `WHEELCAM_VLM_URL`; inspect that runtime's configuration before use.

Supply secrets through the environment or your secret manager, never committed examples. Photos may leave the device when the configured endpoint is remote. Confirm permission for that endpoint and those inputs. Deterministic photo reconstruction can run without requesting model-based styling.

The skill does not deploy models. Run it on the intended machine to place CAD computation there. A remote model endpoint alone does not relocate CAD computation.

## Optional LocalPilot preparation

If the separately installed `localpilot` integration skill is available, use it to prepare or assess a supported inference target and obtain an actual engine endpoint. It is optional; existing compatible services work directly. The upstream authoritative skill is `local-ai-autopilot`. LocalPilot is not a driver or inference-engine installer. Keep image/history traffic on the underlying engine until its gateway is verified for those modalities.
