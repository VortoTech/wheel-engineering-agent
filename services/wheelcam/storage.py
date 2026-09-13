import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import TEMPLATE_VERSION, WheelSpec, default_sources, migrate_spec


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


class Store:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "images").mkdir(exist_ok=True)
        (self.root / "models").mkdir(exist_ok=True)
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    spec TEXT NOT NULL, sources TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    primary_image_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS images (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    status TEXT NOT NULL, snapshot TEXT NOT NULL, report TEXT,
                    error TEXT, created_at TEXT NOT NULL, finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_images_project ON images(project_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_project ON jobs(project_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, created_at);
                CREATE TABLE IF NOT EXISTS image_analyses (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    image_id TEXT NOT NULL REFERENCES images(id), result TEXT NOT NULL, created_at TEXT NOT NULL
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(projects)")}
            if "preparation" not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN preparation TEXT NOT NULL DEFAULT '{}'")
            if "applied_analysis_id" not in columns:
                db.execute("ALTER TABLE projects ADD COLUMN applied_analysis_id TEXT")
            # Drafts saved under an older template are upgraded once; job snapshots stay untouched.
            for row in db.execute("SELECT id, spec, sources FROM projects").fetchall():
                migrated = migrate_spec(json.loads(row["spec"]), json.loads(row["sources"]))
                if migrated:
                    spec, sources = migrated
                    db.execute("UPDATE projects SET spec=?,sources=?,revision=revision+1,updated_at=? WHERE id=?",
                               (json.dumps(spec), json.dumps(sources, ensure_ascii=False), now(), row["id"]))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / "wheelcam.sqlite3", timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def create_project(self, name, preset="single"):
        from .presets import preset_spec
        spec, sources = preset_spec(preset)
        project_id = uid()
        timestamp = now()
        with self.connection() as db:
            db.execute("INSERT INTO projects(id,name,spec,sources,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                       (project_id, name.strip() or "未命名轮毂", spec.model_dump_json(),
                        json.dumps(sources, ensure_ascii=False), timestamp, timestamp))
        return self.project(project_id)

    def project(self, project_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise KeyError(project_id)
            result = dict(row)
            result["spec"] = json.loads(result["spec"])
            result["sources"] = json.loads(result["sources"])
            result["preparation"] = json.loads(result["preparation"])
            result["case_selection"] = json.loads(result["case_selection"]) if result.get("case_selection") else None
            result["images"] = [dict(image) for image in db.execute(
                "SELECT * FROM images WHERE project_id=? ORDER BY created_at", (project_id,))]
            result["jobs"] = [self.job_dict(job) for job in db.execute(
                "SELECT * FROM jobs WHERE project_id=? ORDER BY created_at DESC", (project_id,))]
            analysis = db.execute("SELECT result FROM image_analyses WHERE project_id=? AND image_id=? ORDER BY created_at DESC LIMIT 1",
                                  (project_id, row["primary_image_id"])).fetchone()
            result["photo_analysis"] = json.loads(analysis[0]) if analysis else None
            return result

    @staticmethod
    def job_dict(row):
        result = dict(row)
        result["snapshot"] = json.loads(result["snapshot"])
        result["report"] = json.loads(result["report"]) if result["report"] else None
        return result

    def enqueue(self, project_id, expected_revision):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise KeyError(project_id)
            if row["revision"] != expected_revision:
                raise ValueError("项目已在其他窗口更新，请重新载入后生成。")
            pending = db.execute("SELECT id FROM jobs WHERE project_id=? AND status IN ('queued','running')",
                                 (project_id,)).fetchone()
            if pending:
                raise ValueError("该项目已有模型正在生成。")
            references = [dict(image) for image in db.execute(
                "SELECT * FROM images WHERE project_id=? ORDER BY created_at", (project_id,))]
            snapshot = {
                "name": row["name"], "spec": json.loads(row["spec"]),
                "sources": json.loads(row["sources"]), "draft_revision": row["revision"],
                "preparation": json.loads(row["preparation"]),
                "template_version": TEMPLATE_VERSION, "reference_images": references,
                "primary_image_id": row["primary_image_id"],
                "image_usage": "manual_reference_only",
            }
            job_id = uid()
            if row["applied_analysis_id"]:
                analysis = db.execute("SELECT result FROM image_analyses WHERE id=? AND project_id=?",
                                      (row["applied_analysis_id"], project_id)).fetchone()
                if analysis:
                    snapshot["photo_analysis"] = json.loads(analysis[0])
                    snapshot["image_usage"] = "local_candidates_reviewed_before_apply"
            snapshot["model_id"] = job_id
            if "case_selection" in row.keys() and row["case_selection"]:
                snapshot["case_selection"] = json.loads(row["case_selection"])
            db.execute("INSERT INTO jobs(id,project_id,status,snapshot,created_at) VALUES(?,?,?,?,?)",
                       (job_id, project_id, "queued", json.dumps(snapshot, ensure_ascii=False), now()))
        return job_id
