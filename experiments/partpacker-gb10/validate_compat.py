"""Small deterministic checks for the ARM64 PartPacker compatibility image."""
import importlib.metadata
import numpy as np
import kiui
import mcubes
import torch
import transformers
import trimesh
from wheelcam_mesh_compat import clean_mesh, decimate_mesh

for name in ("read_image", "write_image", "seed_everything"):
    assert callable(getattr(kiui, name, None)), f"kiui.{name} is unavailable"
assert hasattr(mcubes, "marching_cubes")
print(
    "runtime packages",
    {
        "kiui": importlib.metadata.version("kiui"),
        "torch": torch.__version__,
        "transformers": transformers.__version__,
        "trimesh": trimesh.__version__,
    },
)

# Exercise the compiled ARM64 PyMCubes extension, rather than checking only that
# its Python symbol exists. A radius-eight sphere should produce one closed
# genus-zero component with bounds strictly inside the 32-cubed test volume.
n = 32
x, y, z = np.mgrid[:n, :n, :n]
field = 8.0 - np.sqrt((x - 15.5) ** 2 + (y - 15.5) ** 2 + (z - 15.5) ** 2)
mc_vertices, mc_faces = mcubes.marching_cubes(field, 0.0)
mc_mesh = trimesh.Trimesh(mc_vertices, mc_faces, process=False)
assert len(mc_vertices) == 1248
assert len(mc_faces) == 2492
assert mc_mesh.is_watertight
assert mc_mesh.euler_number == 2
assert len(mc_mesh.split(only_watertight=False)) == 1
assert np.all(mc_mesh.bounds[0] > 0) and np.all(mc_mesh.bounds[1] < n - 1)

vertices = np.array([
    [0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [9, 9, 9], [9.001, 9, 9], [9, 9.001, 9]
], dtype=float)
faces = np.array([[0, 1, 2], [0, 2, 3], [0, 1, 2], [4, 5, 6]], dtype=int)
clean_vertices, clean_faces = clean_mesh(vertices, faces, min_f=2, min_d=0, verbose=False)
assert len(clean_faces) == 2
assert len(clean_vertices) == 4
try:
    decimate_mesh(clean_vertices, clean_faces, 1)
except NotImplementedError:
    pass
else:
    raise AssertionError("decimation must fail explicitly")
print("ARM64 mesh compatibility checks passed")
