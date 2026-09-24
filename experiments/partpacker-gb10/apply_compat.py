"""Apply narrow WheelCAM ARM64/offline patches to a copied PartPacker tree."""
from pathlib import Path
import shutil
import sys

root = Path(sys.argv[1]).resolve()

mesh_target = root / "vae" / "utils.py"
mesh_old = "from kiui.mesh_utils import clean_mesh, decimate_mesh"
mesh_new = "from wheelcam_mesh_compat import clean_mesh, decimate_mesh"
mesh_text = mesh_target.read_text()
if mesh_text.count(mesh_old) != 1:
    raise SystemExit("unexpected PartPacker vae/utils.py; refusing to patch")
mesh_target.write_text(mesh_text.replace(mesh_old, mesh_new))
shutil.copyfile(Path(__file__).with_name("wheelcam_mesh_compat.py"), root / "wheelcam_mesh_compat.py")

infer_target = root / "flow" / "scripts" / "infer.py"
infer_text = infer_target.read_text()
import_old = "import rembg"
import_new = "rembg = None  # WheelCAM: RGBA-only experiment; no runtime model download"
session_old = "bg_remover = rembg.new_session()"
session_new = "bg_remover = None  # WheelCAM: keep RGBA inference fully offline"
remove_old = "input_image = rembg.remove(input_image, session=bg_remover)  # [H, W, 4]"
remove_new = 'raise RuntimeError("WheelCAM GB10 experiment requires pre-matted RGBA input")'
if (infer_text.count(import_old) != 1 or infer_text.count(session_old) != 1
        or infer_text.count(remove_old) != 1):
    raise SystemExit("unexpected PartPacker flow/scripts/infer.py; refusing to patch")
infer_target.write_text(
    infer_text.replace(import_old, import_new).replace(session_old, session_new).replace(remove_old, remove_new)
)

model_target = root / "flow" / "model.py"
model_text = model_target.read_text()
dino_old = 'Dinov2Model.from_pretrained("facebook/dinov2-giant")'
dino_new = 'Dinov2Model.from_pretrained("/weights/dinov2-giant", local_files_only=True)'
if model_text.count(dino_old) != 1:
    raise SystemExit("unexpected PartPacker flow/model.py; refusing to patch")
model_target.write_text(model_text.replace(dino_old, dino_new))

flow_config_target = root / "flow" / "configs" / "big_parts_strict_pvae.py"
flow_config_text = flow_config_target.read_text()
vae_old = 'vae_ckpt_path="pretrained/vae.pt"'
vae_new = 'vae_ckpt_path="/weights/vae.pt"'
if flow_config_text.count(vae_old) != 1:
    raise SystemExit("unexpected PartPacker flow config; refusing to patch VAE path")
flow_config_target.write_text(flow_config_text.replace(vae_old, vae_new))

print("Applied conservative ARM64 mesh, offline VAE/DINOv2, and RGBA-only patches")
