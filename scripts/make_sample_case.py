"""A public sample order for reproducing the demo chain without private data or a model.

    PYTHONPATH=services python scripts/make_sample_case.py            # -> examples/sample-order/

The wheel is built from a preset in experiments/forged-blank/recipes (no customer geometry), rendered
straight on as a dark wheel on white, and written as the order's front image beside its confirmed
specification. The recipe it came from is kept as sample_truth.json (not truth.json, which the chain
reads as factory CAD), so the reconstruction can be checked
against a known answer (spoke count, dimensions). No vision or language model is called.
"""
import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MESSAGE = "20×9 ET35，5×114.3，中心孔 73.1，孔型 14X28X60。做 5 辐直辐。"


def make(out: Path, size=1400):
    os.environ["WHEELCAM_STYLE_LIBRARY"] = str(out / "no-private-library.json")   # public presets only
    import numpy as np
    from PIL import Image
    from wheelcam import mesh_build, text_wheel
    from wheelcam.front_render import render_front
    decision = text_wheel.review(MESSAGE, {})              # deterministic: no model proposal
    if decision["style_template"]["kind"] != "parametric":
        raise RuntimeError("sample must come from a public parametric preset")
    recipe, spec, form = decision["recipe"], decision["spec"], decision["hole_form"]
    body, _ = mesh_build.build(recipe)
    grey = render_front(body, size=size).astype(float)
    wheel = grey < 250                                        # darken like a black wheel in a studio shot
    img = np.where(wheel, grey * 0.45 + 8, 255).clip(0, 255).astype(np.uint8)
    pad = size // 14
    canvas = np.full((size + 2 * pad, size + 2 * pad), 255, np.uint8)
    canvas[pad:pad + size, pad:pad + size] = img
    out.mkdir(parents=True, exist_ok=True)
    Image.fromarray(canvas).convert("RGB").save(out / "front.jpg", quality=92)
    (out / "spec.json").write_text(json.dumps({
        "spec": spec, "hole_form": form,
        "spec_evidence": {k: {"source": "drawing", "reference": "public sample order"} for k in spec},
        "note": "公开示例订单：由仓库内参数预设生成，不含任何客户数据"}, ensure_ascii=False, indent=1))
    (out / "sample_truth.json").write_text(json.dumps({"source": "experiments/forged-blank/recipes (public preset)",
                                                "message": MESSAGE, "recipe": recipe}, ensure_ascii=False, indent=1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "examples/sample-order")
    a = ap.parse_args()
    print(make(a.out))


if __name__ == "__main__":
    main()
