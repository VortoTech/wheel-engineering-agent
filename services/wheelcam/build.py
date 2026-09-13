"""Subprocess entry point isolates the CAD kernel from the API."""
import json
import sys
from pathlib import Path

from .models import Preparation, WheelSpec


if __name__ == "__main__":
    snapshot = json.loads(Path(sys.argv[1]).read_text())
    # Keep first-time native-library loading separate from geometry execution time.
    from .geometry import export_model
    (Path(sys.argv[2]) / "kernel.ready").touch()
    export_model(WheelSpec.model_validate(snapshot["spec"]), Path(sys.argv[2]),
                 Preparation.model_validate(snapshot.get("preparation", {})), snapshot)
