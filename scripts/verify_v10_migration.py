"""Verify a v9-to-v10 migration on a temporary copy without touching live data."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from wheelcam.models import WheelSpec
from wheelcam.storage import Store


HISTORY_TABLES = ("images", "jobs", "image_analyses", "visual_cases", "contour_evaluations")


def rows(db_path: Path, table: str):
    with sqlite3.connect(db_path) as db:
        columns = [item[1] for item in db.execute(f"PRAGMA table_info({table})")]
        order = "id" if "id" in columns else "rowid"
        return db.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()


def projects(db_path: Path):
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(item) for item in db.execute("SELECT * FROM projects ORDER BY id")]


def file_hashes(root: Path):
    result = {}
    for folder in ("images", "models"):
        base = root / folder
        if not base.exists():
            continue
        for path in sorted(item for item in base.rglob("*") if item.is_file()):
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("before_db", type=Path, help="SQLite database captured before the v10 migration")
    parser.add_argument("source_data", type=Path, help="Data directory whose images/models are copied for hash checks")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="wheelcam-v10-migration-") as temporary:
        root = Path(temporary)
        shutil.copy2(args.before_db.resolve(), root / "wheelcam.sqlite3")
        for folder in ("images", "models"):
            source = args.source_data.resolve() / folder
            if source.exists():
                shutil.copytree(source, root / folder)

        database = root / "wheelcam.sqlite3"
        before_projects = projects(database)
        before_history = {table: rows(database, table) for table in HISTORY_TABLES}
        before_files = file_hashes(root)

        Store(root)
        after_projects = projects(database)
        after_history = {table: rows(database, table) for table in HISTORY_TABLES}
        after_files = file_hashes(root)

        assert before_history == after_history, "历史记录在草稿迁移期间发生变化"
        assert before_files == after_files, "图片或模型文件在草稿迁移期间发生变化"
        assert len(before_projects) == len(after_projects)
        for old, new in zip(before_projects, after_projects, strict=True):
            assert old["id"] == new["id"]
            for key in old.keys() - {"spec", "sources", "revision", "updated_at"}:
                assert old[key] == new[key]
            old_spec, new_spec = json.loads(old["spec"]), json.loads(new["spec"])
            old_sources, new_sources = json.loads(old["sources"]), json.loads(new["sources"])
            WheelSpec.model_validate(new_spec)
            assert all(new_spec[key] == value for key, value in old_spec.items())
            assert all(new_sources[key] == value for key, value in old_sources.items())
            changed = set(old_spec) != set(WheelSpec.model_fields)
            assert new["revision"] == old["revision"] + int(changed)

        stable_projects = projects(database)
        Store(root)
        assert projects(database) == stable_projects, "第二次启动重复迁移了草稿"
        assert {table: rows(database, table) for table in HISTORY_TABLES} == after_history
        assert file_hashes(root) == after_files

        print(json.dumps({
            "projects": len(after_projects),
            "history_rows": {table: len(value) for table, value in after_history.items()},
            "files_preserved": len(after_files),
            "one_time_migration": True,
            "template_version": "forged-monoblock-v10",
        }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
