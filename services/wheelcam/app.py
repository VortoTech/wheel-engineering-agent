import io
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import BuildRequest, DraftUpdate, ProjectCreate, TEMPLATE_VERSION, WheelSpec
from .storage import Store, now, uid
from .worker import Worker

ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: Path | None = None, start_worker=True):
    store = Store(data_dir or Path(os.getenv("WHEELCAM_DATA_DIR", str(ROOT / "data"))))
    worker = Worker(store)

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="WheelCAM", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin:
            if origin not in {"http://127.0.0.1:5178", "http://localhost:5178",
                               str(request.base_url).rstrip("/")}:
                return JSONResponse({"detail": "仅允许本地工作台写入。"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "项目或文件不存在。"}, status_code=404)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "template_version": TEMPLATE_VERSION,
                "capabilities": {"parametric_cad": True, "image_inference": False, "cam": False,
                                 "preparation": True, "feature_export": True}}

    @app.get("/api/template")
    def template():
        return {"version": TEMPLATE_VERSION, "schema": WheelSpec.model_json_schema()}

    @app.get("/api/projects")
    def projects():
        with store.connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,name,revision,updated_at FROM projects ORDER BY created_at DESC")]

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate):
        return store.create_project(body.name)

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str):
        return store.project(project_id)

    @app.put("/api/projects/{project_id}")
    def update_project(project_id: str, body: DraftUpdate):
        previous = store.project(project_id)
        with store.connection() as db:
            result = db.execute(
                "UPDATE projects SET name=?,spec=?,sources=?,preparation=?,revision=revision+1,updated_at=? WHERE id=? AND revision=?",
                (body.name.strip() or "未命名轮毂", body.spec.model_dump_json(),
                 json.dumps({key: source.model_dump() for key, source in body.sources.items()}, ensure_ascii=False),
                 body.preparation.model_dump_json() if "preparation" in body.model_fields_set else json.dumps(previous["preparation"]),
                 now(), project_id, body.expected_revision))
            if result.rowcount != 1:
                raise HTTPException(409, "项目已在其他窗口更新，请重新载入，当前修改尚未保存。")
        return store.project(project_id)

    @app.post("/api/projects/{project_id}/builds", status_code=202)
    def generate(project_id: str, body: BuildRequest):
        try:
            job_id = store.enqueue(project_id, body.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job_id, "status": "queued"}

    @app.post("/api/projects/{project_id}/images", status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...)):
        store.project(project_id)
        content = await file.read(10 * 1024 * 1024 + 1)
        if len(content) > 10 * 1024 * 1024:
            raise HTTPException(413, "每张图片请小于 10 MB。")
        try:
            with Image.open(io.BytesIO(content)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("unsupported format")
                if original.width * original.height > 30_000_000:
                    raise ValueError("too many pixels")
                normalized = ImageOps.exif_transpose(original).convert("RGB")
                normalized.thumbnail((2400, 2400))
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(422, "请选择有效的 JPG、PNG 或 WebP 图片（不超过 3000 万像素）。")
        image_id = uid()
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute("SELECT count(*) FROM images WHERE project_id=?", (project_id,)).fetchone()[0]
            if count >= 20:
                raise HTTPException(422, "单个项目最多保存 20 张参考图片。")
            normalized.save(store.root / "images" / f"{image_id}.jpg", "JPEG", quality=90)
            (store.root / "images" / f"{image_id}.source").write_bytes(content)
            db.execute("INSERT INTO images(id,project_id,name,sha256,created_at) VALUES(?,?,?,?,?)",
                       (image_id, project_id, Path(file.filename or "参考图片").name[:120], hashlib.sha256(content).hexdigest(), now()))
            db.execute("UPDATE projects SET primary_image_id=COALESCE(primary_image_id,?),revision=revision+1,updated_at=? WHERE id=?",
                       (image_id, now(), project_id))
        return store.project(project_id)

    @app.put("/api/projects/{project_id}/primary/{image_id}")
    def primary(project_id: str, image_id: str):
        with store.connection() as db:
            image = db.execute("SELECT id FROM images WHERE id=? AND project_id=?", (image_id, project_id)).fetchone()
            if not image:
                raise HTTPException(404, "参考图片不存在。")
            db.execute("UPDATE projects SET primary_image_id=?,revision=revision+1,updated_at=? WHERE id=?",
                       (image_id, now(), project_id))
        return store.project(project_id)

    @app.get("/api/images/{image_id}")
    def image(image_id: str):
        with store.connection() as db:
            if not db.execute("SELECT id FROM images WHERE id=?", (image_id,)).fetchone():
                raise HTTPException(404, "图片不存在。")
        return FileResponse(store.root / "images" / f"{image_id}.jpg", media_type="image/jpeg")

    @app.get("/api/builds/{job_id}/{artifact}")
    def artifact(job_id: str, artifact: str):
        names = {"step": "wheel.step", "glb": "wheel.glb", "recipe": "recipe.json", "report": "report.json",
                 "features": "features.json", "operations": "operations.csv", "handoff": "handoff.zip",
                 "stock": "stock.step", "caliper": "caliper-envelope.step"}
        if artifact not in names:
            raise HTTPException(404, "文件不存在。")
        with store.connection() as db:
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != "succeeded":
                raise HTTPException(404, "该版本没有可用的导出文件。")
        path = store.root / "models" / job_id / names[artifact]
        if not path.exists():
            raise HTTPException(404, "导出文件缺失，请重新生成。")
        return FileResponse(path, filename=f"wheelcam-{job_id[:8]}-{names[artifact]}",
                            media_type="model/gltf-binary" if artifact == "glb" else "application/octet-stream",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    frontend = ROOT / "apps" / "web" / "dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
