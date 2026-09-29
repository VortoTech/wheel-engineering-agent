---
name: localpilot
description: Connect Wheel Engineering to an inference service prepared or evaluated by LocalPilot. Use for LocalPilot handoff, node prerequisite assessment and model endpoint verification before wheel tasks.
---

# LocalPilot integration for Wheel Engineering

This is a thin integration skill. The authoritative inference configuration workflow is LocalPilot's `local-ai-autopilot` skill; do not duplicate its planner, benchmarks or service control here.

## Workflow

1. If the user already has a suitable model endpoint, inspect and test it directly. LocalPilot is optional.
2. If LocalPilot preparation is needed, locate the authorized checkout through `LOCALPILOT_PROJECT`. Read its `.agents/skills/local-ai-autopilot/SKILL.md`, then follow its supported hardware, CLI and evidence requirements. Do not assume a saved READY profile proves a live service.
3. Obtain the actual engine endpoint, served model ID, execution target and acceptance report from that workflow. Prefer the engine endpoint directly: the inspected LocalPilot gateway flattens message content and drops assistant turns, so it is not a verified image or multi-turn proxy.
4. Run `python3 <skill-dir>/scripts/check_endpoint.py --base-url http://127.0.0.1:8000/v1 --model ACTUAL_MODEL --probe-text` with a non-sensitive prompt. This verifies discovery and basic text response only; image and wheel task acceptance remain separate.
5. Apply the returned CHAT settings to the process running Wheel Engineering. Configure VLM separately only after a real image request passes. Resume the wheel skill's build and verification workflow; do not claim a model smoke test as CAD success.

Read [setup.md](references/setup.md) for new-node prerequisites and source provenance. Missing tools or service failures should produce an actionable gap report, not an unapproved install, mock fallback or replacement of existing workloads. Keep credentials in environment variables. Do not print full configuration environments.
