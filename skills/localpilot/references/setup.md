# LocalPilot source and new-node handoff

## Authoritative source

Set `LOCALPILOT_PROJECT` to an authorized LocalPilot checkout. Its canonical skill is `.agents/skills/local-ai-autopilot/SKILL.md` (name `local-ai-autopilot`, CLI contract 0.2.x). The source inspected for this integration was commit `88be21a36749db5c0ab8b5882f37da793f984a58`.

The inspected checkout has no Git remote configured. No repository URL is guessed here. Once the owner supplies or publishes its authorized repository URL, record it here with the supported revision. This integration package grants no additional access to that project and does not copy its implementation.

## What a new node needs

| Layer | Required preparation |
| --- | --- |
| Machine | Supported Apple Silicon or NVIDIA/CUDA node, access and sufficient resources |
| System | Python, driver/device access where needed, compatible container or native environment |
| Inference | Installed supported engine; model weights or authorized download access |
| LocalPilot | CLI installed on the execution node, engine configuration and task acceptance settings |
| Wheel runtime | Separately installed CAD dependencies; required only where CAD will execute |

LocalPilot is not a bare-machine provisioner. Its canonical skill excludes engine installation. It can inspect supported hardware, plan/measure candidate configurations and hand off an endpoint. Launching a supported installed engine is opt-in through `LOCALPILOT_ALLOW_ENGINE_LAUNCH=1`; it may download weights. Enable only for an authorized deployment with known resource and recovery requirements.

For an installed CLI, inspect `localpilot --version`, `localpilot doctor` and `localpilot engines` on the intended target. SSH execution requires the remote CLI too. Use the upstream skill for candidate acceptance, not a generic guessed autopilot goal. Mock mode is simulated and cannot pass real-device acceptance.

## Existing endpoint handoff

```bash
python3 skills/localpilot/scripts/check_endpoint.py \
  --base-url http://127.0.0.1:8000/v1 --model actual-served-model --probe-text
```

If the engine requires a token, set `WHEELCAM_CHAT_API_KEY`; the script does not export it. The result includes CHAT configuration and explicitly labels visual validation as not performed. Apply configuration before starting the workbench, then execute a real wheel task with its normal checks.

On SSH targets, the node's loopback address is not the workstation's loopback address. Use an authorized tunnel or network route. This does not move CAD execution to the GPU node.

The checker never installs a model, launches LocalPilot, edits a profile or restarts an engine. An endpoint may be provided by LocalPilot or by another operator; successful probing alone does not establish its deployment provenance.

## Integration verification (2026-09-28)

The endpoint contract and portable Wheel Skill/privacy tests passed (15 tests total). A live text probe through the previously used local Spark tunnel could not connect (`URLError`); no live endpoint or fresh-node deployment acceptance is claimed for this integration revision. No model service was replaced or restarted.
