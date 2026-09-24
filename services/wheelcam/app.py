import io
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .models import RootCorrectionRequest, AnalysisRequest, BuildRequest, DraftUpdate, ForgedBuildRequest, ForgedPhotoFitRequest, ProjectCreate, TEMPLATE_VERSION, WheelSpec
from .agent_cad import AgentCadPlan, AgentPlanApply, ConfirmedEvidenceConflict, evaluate_plan
from .agent_orchestrator import AgentProposalRequest, OpenAICompatibleAgentProvider, provider_status
from .storage import Store, now, uid
from .worker import Worker
from .sf3d import status as sf3d_status

ROOT = Path(__file__).resolve().parents[2]


def create_app(data_dir: Path | None = None, start_worker=True, agent_provider=None):
    store = Store(data_dir or Path(os.getenv("WHEELCAM_DATA_DIR", str(ROOT / "data"))))
    from .case_library import install, routes
    install(store)
    worker = Worker(store)
    agent_provider = agent_provider or OpenAICompatibleAgentProvider.from_environment()

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            worker.start()
        yield
        if start_worker:
            worker.stop()

    app = FastAPI(title="WheelCAM", version="0.15.0", lifespan=lifespan)
    app.state.store = store
    app.include_router(routes(store))
    from .contour_review import routes as contour_routes
    app.include_router(contour_routes(store))
    from .sector_study import routes as sector_routes
    app.include_router(sector_routes(store))
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

    @app.exception_handler(ConfirmedEvidenceConflict)
    async def confirmed_evidence_locked(request, exc):
        return JSONResponse({"detail": exc.detail}, status_code=409)

    @app.get("/api/health")
    def health():
        reconstruction = sf3d_status()
        agent = provider_status(agent_provider)
        return {"status": "ok", "template_version": TEMPLATE_VERSION,
                "capabilities": {"parametric_cad": True, "agent_cad_ir": True, "agent_model_provider": agent["configured"], "image_inference": reconstruction["available"], "local_image_candidates": True, "continuous_spoke_contours": True, "photo_pose_fit": True, "editable_root_points": True, "window_fit": True, "window_method": True, "independent_rim_pockets": True, "cam": False,
                                 "case_library": True, "contour_review": True, "preparation": True, "feature_export": True, "window_side_draft": True, "window_face_relief": True, "window_spoke_ridge": True},
                "visual_reconstruction": reconstruction, "agent": agent}

    @app.get("/api/agent-cad/status")
    def agent_cad_status():
        return provider_status(agent_provider)

    @app.get("/api/reconstruction/status")
    def reconstruction_status():
        return sf3d_status()

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
        return store.create_project(body.name, body.preset)

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

    @app.post("/api/projects/{project_id}/agent-cad/preview")
    def preview_agent_cad(project_id: str, plan: AgentCadPlan):
        project = store.project(project_id)
        if project["revision"] != plan.base_revision:
            raise HTTPException(409, "Agent 计划基于旧草稿，请读取最新 revision 后重新规划。")
        return evaluate_plan(project["spec"], project["sources"], plan)

    @app.post("/api/projects/{project_id}/agent-cad/propose")
    def propose_agent_cad(project_id: str, body: AgentProposalRequest):
        if agent_provider is None:
            raise HTTPException(503, provider_status(None)["reason"])
        project = store.project(project_id)
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "草稿已改变，请让 Agent 基于最新 revision 重新规划。")
        image_path = None
        if body.include_primary_image and project.get("primary_image_id"):
            image_path = store.root / "images" / f"{project['primary_image_id']}.jpg"
        try:
            from .agent_tools import propose_with_tools
            plan, tool_trace = propose_with_tools(agent_provider, store, project, body.goal, image_path)
            if plan.base_revision != project["revision"]:
                raise ValueError("Agent 返回的 base_revision 与当前草稿不一致。")
            preview = evaluate_plan(project["spec"], project["sources"], plan)
        except ConfirmedEvidenceConflict:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        if store.project(project_id)["revision"] != body.expected_revision:
            raise HTTPException(409, "分析期间草稿已改变，请重新规划。")
        return {"provider": provider_status(agent_provider), "plan": plan, "preview": preview,
                "tool_trace": tool_trace}

    @app.post("/api/projects/{project_id}/agent-cad/apply")
    def apply_agent_cad(project_id: str, body: AgentPlanApply):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                raise KeyError(project_id)
            if row["revision"] != body.plan.base_revision:
                raise HTTPException(409, "Agent 计划基于旧草稿，请读取最新 revision 后重新规划。")
            result = evaluate_plan(
                json.loads(row["spec"]), json.loads(row["sources"]), body.plan,
                body.approved_action_ids,
            )
            if result["pending_approval_action_ids"]:
                raise HTTPException(409, {
                    "message": "关键工程参数或草图修改需要明确批准。",
                    "pending_approval_action_ids": result["pending_approval_action_ids"],
                })
            resulting_revision = row["revision"] + 1
            run_id, timestamp = uid(), now()
            db.execute(
                "UPDATE projects SET spec=?,sources=?,applied_analysis_id=NULL,revision=?,updated_at=? WHERE id=?",
                (json.dumps(result["proposed_spec"], ensure_ascii=False),
                 json.dumps(result["proposed_sources"], ensure_ascii=False),
                 resulting_revision, timestamp, project_id),
            )
            db.execute(
                "INSERT INTO agent_cad_runs VALUES(?,?,?,?,?,?,?)",
                (run_id, project_id, body.plan.base_revision, resulting_revision,
                 body.plan.model_dump_json(), json.dumps(result, ensure_ascii=False), timestamp),
            )
        return {"agent_run_id": run_id, "result": result, "project": store.project(project_id)}

    @app.post("/api/projects/{project_id}/builds", status_code=202)
    def generate(project_id: str, body: BuildRequest):
        try:
            job_id = store.enqueue(project_id, body.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job_id, "status": "queued"}

    @app.get("/api/forged/presets")
    def forged_presets():
        from dataclasses import asdict
        from .forged_blank import ForgedWheel, presets
        return {"defaults": asdict(ForgedWheel()), "presets": presets()}

    @app.post("/api/forged/photo-fit")
    def forged_photo_fit(body: ForgedPhotoFitRequest):
        from .forged_photo import fit_recipe
        try:
            recipe, report = fit_recipe(body.base_recipe, body.rim_points, body.hub_point, body.windows, body.groups, body.bolts)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        report["image_id"] = body.image_id
        return {"recipe": recipe, "report": report}

    @app.post("/api/projects/{project_id}/forged-builds", status_code=202)
    def generate_forged(project_id: str, body: ForgedBuildRequest):
        from dataclasses import asdict
        from .forged_blank import recipe_from_dict
        try:
            recipe = asdict(recipe_from_dict(body.recipe))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            job_id = store.enqueue(project_id, body.expected_revision, forged=recipe)
        except KeyError as exc:
            raise HTTPException(404, "项目不存在。") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job_id, "status": "queued", "template": "forged-blank-v1"}

    @app.post("/api/projects/{project_id}/reconstructions", status_code=202)
    def reconstruct(project_id: str, body: BuildRequest):
        state = sf3d_status()
        if not state["available"]:
            raise HTTPException(503, state["reason"])
        try:
            job_id = store.enqueue_reconstruction(project_id, body.expected_revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": job_id, "status": "queued"}

    @app.post("/api/projects/{project_id}/images/{image_id}/analyze")
    def analyze(project_id: str, image_id: str, body: AnalysisRequest):
        from .vision import detect
        project = store.project(project_id)
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "草稿已变更，请重新载入后识图。")
        ref = next((i for i in project["images"] if i["id"] == image_id), None)
        if not ref:
            raise HTTPException(404, "参考图不属于当前项目。")
        try:
            result = detect(store.root / "images" / f"{image_id}.jpg", WheelSpec(**project["spec"]), body.reference_outer_mm)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        result.update(id=uid(), image_id=image_id, image_sha256=ref["sha256"],
                      base_revision=project["revision"], base_spec=project["spec"], created_at=now())
        with store.connection() as db:
            db.execute("INSERT INTO image_analyses VALUES(?,?,?,?,?)", (result["id"], project_id, image_id,
                       json.dumps(result, ensure_ascii=False), result["created_at"]))
        return result

    @app.post("/api/projects/{project_id}/images/{image_id}/window-fit")
    def window_fit(project_id: str, image_id: str, body: BuildRequest):
        """Fit window-method outlines from the annotation tool's label for this exact image file."""
        from .window_fit import candidate
        project = store.project(project_id)
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "草稿已变更，请重新载入后拟合。")
        ref = next((i for i in project["images"] if i["id"] == image_id), None)
        if not ref:
            raise HTTPException(404, "参考图不属于当前项目。")
        labels = Path(os.getenv("WHEELCAM_LABEL_DIR", str(store.root / "annotations")))
        path = labels / f"{ref['sha256']}.json"
        if not path.exists():
            raise HTTPException(404, "这张图还没有窗口标注。请先用标注工具（experiments/annotate）标出外圈和窗口，并填写组数。")
        try:
            label = json.loads(path.read_text())
            if not label.get("rim") or not label.get("windows") or not (label.get("meta") or {}).get("groups"):
                raise ValueError("标注缺少外圈、窗口或组数。")
            result = candidate(label, WheelSpec(**project["spec"]))
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        result.update(id=uid(), image_id=image_id, image_sha256=ref["sha256"],
                      base_revision=project["revision"], base_spec=project["spec"], created_at=now())
        with store.connection() as db:
            db.execute("INSERT INTO image_analyses VALUES(?,?,?,?,?)", (result["id"], project_id, image_id,
                       json.dumps(result, ensure_ascii=False), result["created_at"]))
        return result

    @app.post("/api/projects/{project_id}/analyses/{analysis_id}/root")
    def refine_root(project_id: str, analysis_id: str, body: RootCorrectionRequest):
        from .root_fitting import correct_root, landmarks
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            saved = db.execute("SELECT result,image_id FROM image_analyses WHERE id=? AND project_id=?", (analysis_id,project_id)).fetchone()
            if row is None or saved is None:
                raise HTTPException(404, "项目或识图记录不存在。")
            result = json.loads(saved["result"])
            applied = row["applied_analysis_id"] == analysis_id and row["revision"] == result["base_revision"]+1
            if row["revision"] != body.expected_revision or (result["base_revision"] != row["revision"] and not applied):
                raise HTTPException(409, "草稿已改变，请重新识图后修正点位。")
            if saved["image_id"] != row["primary_image_id"] or not result.get("camera_fit") or not result["can_apply"]:
                raise HTTPException(422, "需要当前主参考图的有效双辐识图记录。")
            try:
                spec = WheelSpec(**{**json.loads(row["spec"]), **result["suggested_parameters"]})
                parameters, fitted_points = correct_root(result, spec, body.group, body.points)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            result.update(id=uid(), parent_analysis_id=analysis_id, base_revision=row["revision"],
                          base_spec=json.loads(row["spec"]), created_at=now())
            result["suggested_parameters"].update(parameters)
            result["root_fit"] = {"status":"manual", "parameters":parameters, "group":body.group,
                                  "points":fitted_points, "edited_points":body.points,
                                  "note":"人工修正点位，按对称与 CAD 约束拟合；非实测"}
            fitted_spec = WheelSpec(**{**spec.model_dump(), **parameters})
            result["root_fit"]["all_points"] = [landmarks(fitted_spec,result,result["camera_fit"]["pose"],g) for g in range(spec.spoke_count)]
            result["section_fit"] = {**result["section_fit"], "status":"superseded_by_manual_points"}
            db.execute("INSERT INTO image_analyses VALUES(?,?,?,?,?)", (result["id"],project_id,saved["image_id"],
                       json.dumps(result,ensure_ascii=False),result["created_at"]))
        return result

    @app.post("/api/projects/{project_id}/analyses/{analysis_id}/apply")
    def apply_analysis(project_id: str, analysis_id: str, body: BuildRequest):
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            saved = db.execute("SELECT result,image_id FROM image_analyses WHERE id=? AND project_id=?", (analysis_id, project_id)).fetchone()
            if row is None or saved is None:
                raise HTTPException(404, "项目或识图记录不存在。")
            result = json.loads(saved["result"])
            if row["revision"] != body.expected_revision or result["base_revision"] != row["revision"]:
                raise HTTPException(409, "识图后草稿已改变，请基于最新参数重新识图。")
            if saved["image_id"] != row["primary_image_id"] or not result["can_apply"]:
                raise HTTPException(422, "候选尚不适用于当前主参考图或模板，请人工核对。")
            spec = WheelSpec(**{**json.loads(row["spec"]), **result["suggested_parameters"]})
            sources = json.loads(row["sources"])
            for key in result["suggested_parameters"]:
                sources[key] = {"kind": "manual", "note": f"人工确认本地识图候选 {analysis_id[:8]}；图 {result['image_sha256'][:12]}；比例拟合，非实测"}
            db.execute("UPDATE projects SET spec=?,sources=?,applied_analysis_id=?,revision=revision+1,updated_at=? WHERE id=?",
                       (spec.model_dump_json(), json.dumps(sources, ensure_ascii=False), analysis_id, now(), project_id))
        return store.project(project_id)

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

    @app.get("/api/projects/{project_id}/front-reference")
    def front_reference(project_id: str, mode: str = "rectified"):
        """Render a reversible, provenance-bound annotation aid from the source photo."""
        from .front_reference import estimate_tilt_deg, rectify_front, symmetry_guide
        if mode not in {"rectified", "symmetry"}:
            raise HTTPException(422, "正视参考图模式仅支持 rectified 或 symmetry。")
        project = store.project(project_id)
        analysis = project.get("photo_analysis")
        image_id = project.get("primary_image_id")
        if not image_id:
            raise HTTPException(422, "请先选择主参考图。")
        if not analysis or analysis.get("image_id") != image_id or not analysis.get("ellipse"):
            raise HTTPException(422, "请先对当前主参考图提取外圈候选。")
        path = store.root / "images" / f"{image_id}.jpg"
        with Image.open(path) as source:
            result = rectify_front(source, analysis["ellipse"], analysis.get("image_size"))
        if mode == "symmetry":
            groups = int((analysis.get("window_fit") or {}).get("groups")
                         or (analysis.get("spokes") or {}).get("groups")
                         or project["spec"].get("spoke_count", 5))
            result = symmetry_guide(result, groups)
        output = io.BytesIO()
        result.save(output, "PNG", optimize=True)
        tilt = estimate_tilt_deg(analysis["ellipse"])
        return Response(output.getvalue(), media_type="image/png", headers={
            "Cache-Control": "private, max-age=60",
            "X-WheelCAM-Derivation": f"ellipse-to-circle-v1; tilt={tilt:.2f}; mode={mode}",
            "X-WheelCAM-Source": analysis.get("image_sha256", "unknown"),
        })

    @app.get("/api/builds/{job_id}/{artifact}")
    def artifact(job_id: str, artifact: str):
        names = {"step": "wheel.step", "glb": "wheel.glb", "recipe": "recipe.json", "engineering": "engineering.json", "report": "report.json",
                 "features": "features.json", "operations": "operations.csv", "handoff": "handoff.zip",
                 "stock": "stock.step", "caliper": "caliper-envelope.step", "presentation": "presentation.glb", "front": "front.svg"}
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
                            media_type="model/gltf-binary" if artifact in {"glb", "presentation"} else "image/svg+xml" if artifact == "front" else "application/octet-stream",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/reconstructions/{job_id}/{artifact}")
    def reconstruction_artifact(job_id: str, artifact: str):
        names = {"glb": "reference.glb", "report": "report.json"}
        if artifact not in names:
            raise HTTPException(404, "文件不存在。")
        with store.connection() as db:
            row = db.execute("SELECT status FROM reconstruction_jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] != "succeeded":
                raise HTTPException(404, "该视觉重建没有可用文件。")
        path = store.root / "reconstructions" / job_id / names[artifact]
        if not path.is_file():
            raise HTTPException(404, "视觉重建文件缺失，请重新生成。")
        return FileResponse(path, filename=f"wheelcam-visual-{job_id[:8]}-{names[artifact]}",
                            media_type="model/gltf-binary" if artifact == "glb" else "application/json",
                            headers={"Cache-Control": "private, max-age=31536000, immutable"})

    frontend = ROOT / "apps" / "web" / "dist"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
