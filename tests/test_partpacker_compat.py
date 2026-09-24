from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_partpacker_adapter_rewrites_all_offline_weight_paths(tmp_path: Path) -> None:
    root = tmp_path / "PartPacker"
    (root / "vae").mkdir(parents=True)
    (root / "flow" / "scripts").mkdir(parents=True)
    (root / "flow" / "configs").mkdir(parents=True)

    (root / "vae" / "utils.py").write_text(
        "from kiui.mesh_utils import clean_mesh, decimate_mesh\n"
    )
    (root / "flow" / "scripts" / "infer.py").write_text(
        "import rembg\n"
        "bg_remover = rembg.new_session()\n"
        "input_image = rembg.remove(input_image, session=bg_remover)  # [H, W, 4]\n"
    )
    (root / "flow" / "model.py").write_text(
        'Dinov2Model.from_pretrained("facebook/dinov2-giant")\n'
    )
    (root / "flow" / "configs" / "big_parts_strict_pvae.py").write_text(
        'vae_ckpt_path="pretrained/vae.pt"\n'
    )

    adapter = (
        Path(__file__).parents[1]
        / "experiments"
        / "partpacker-gb10"
        / "apply_compat.py"
    )
    subprocess.run([sys.executable, str(adapter), str(root)], check=True)

    assert (root / "wheelcam_mesh_compat.py").is_file()
    assert "from wheelcam_mesh_compat import clean_mesh, decimate_mesh" in (
        root / "vae" / "utils.py"
    ).read_text()
    assert 'from_pretrained("/weights/dinov2-giant", local_files_only=True)' in (
        root / "flow" / "model.py"
    ).read_text()
    assert 'vae_ckpt_path="/weights/vae.pt"' in (
        root / "flow" / "configs" / "big_parts_strict_pvae.py"
    ).read_text()
    patched_infer = (root / "flow" / "scripts" / "infer.py").read_text()
    assert "import rembg" not in patched_infer
    assert "requires pre-matted RGBA input" in patched_infer


def test_partpacker_runtime_fails_closed_when_vae_is_missing() -> None:
    runtime = (
        Path(__file__).parents[1]
        / "experiments"
        / "partpacker-gb10"
        / "run_partpacker.sh"
    ).read_text()

    assert "test -f /weights/vae.pt" in runtime
    assert "python3 /opt/wheelcam/apply_compat.py" in runtime
