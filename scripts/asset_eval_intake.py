"""Inventory private wheel-design references without copying their contents.

The resulting manifest is an intake record, not a runnable reconstruction set:
render-to-variant pairing and drawing specifications still require review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path


KINDS = {".jpg": "render", ".png": "render", ".x_t": "cad_truth",
         ".dwg": "drawing", ".xls": "spreadsheet", ".xlsx": "confirmation"}
MODEL_RE = re.compile(r"\b(HN\d+|M\d+)\b", re.IGNORECASE)
TAIL_RE = re.compile(r"-(\d+)[-Xx]([\d.]+)-\(?(-?\d+)\)?-([\d.]+)\.x_t$", re.IGNORECASE)
SIZE_RE = re.compile(r"(\d{2})[Xx](\d+(?:\.\d+)?)$")
COMPACT_SIZE_RE = re.compile(r"(\d{2})(\d{1,2}(?:\.\d+)?)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cad_filename_spec(name: str) -> dict | None:
    """A filename hint only; dimensions must later be confirmed from a drawing."""
    tail = TAIL_RE.search(name)
    if not tail:
        return None
    prefix = name[:tail.start()]
    size = SIZE_RE.search(prefix) or COMPACT_SIZE_RE.search(prefix)
    if not size:
        return None
    diameter, width = size.groups()
    bolts, pcd, et, bore = tail.groups()
    return {"diameter_in": int(diameter), "width_in": float(width),
            "bolts": int(bolts), "pcd_mm": float(pcd), "et_mm": int(et),
            "center_bore_mm": float(bore), "source": "filename_unverified"}


def inventory(root: Path) -> dict:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"asset root is not a directory: {root}")
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in KINDS:
            continue
        relative = path.relative_to(root)
        folder = next((part for part in relative.parts[:-1] if MODEL_RE.search(part)), "")
        match = MODEL_RE.search(folder)
        record = {"relative_path": relative.as_posix(), "model": match.group(1).upper() if match else None,
                  "kind": KINDS[path.suffix.lower()], "bytes": path.stat().st_size,
                  "sha256": sha256(path)}
        if record["kind"] == "cad_truth":
            record["filename_spec_candidate"] = cad_filename_spec(path.name)
        files.append(record)

    duplicates = defaultdict(list)
    for record in files:
        if record["kind"] == "cad_truth":
            duplicates[record["sha256"]].append(record["relative_path"])
    duplicate_cad = [{"sha256": digest, "paths": paths} for digest, paths in sorted(duplicates.items())
                     if len(paths) > 1]
    models = sorted({record["model"] for record in files if record["model"]})
    summary = {"model_count": len(models), "models": models,
               "file_count": len(files), "kinds": dict(sorted(Counter(r["kind"] for r in files).items())),
               "unique_cad_truth_count": len(duplicates), "duplicate_cad_groups": len(duplicate_cad),
               "unparsed_cad_names": [r["relative_path"] for r in files
                                      if r["kind"] == "cad_truth" and r["filename_spec_candidate"] is None]}
    return {"schema": "wheelcam-asset-intake-v1", "asset_root": str(root), "summary": summary,
            "duplicate_cad": duplicate_cad, "files": files,
            "status": "inventory_only; render crops, drawing confirmation and CAD comparison pending"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="write into a private ignored directory")
    args = parser.parse_args()
    report = inventory(args.asset_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
