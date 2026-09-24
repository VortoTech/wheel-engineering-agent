"""Project-bound experimental surfaces. CAD projects and job histories stay intact."""
import base64
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from PIL import Image

from .sector_surface import SurfaceControls, VERSION, build_surface, to_full_wheel_glb, to_glb
from . import sector_evidence
from .storage import now, uid

EVIDENCE_DIR = Path(__file__).resolve().parents[2] / "experiments" / "sector-study"
# Capture fingerprints with the imported implementation, not whatever source a
# developer may edit on disk while this non-reloading server is still running.
IMPLEMENTATION_FILES = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                        for name in ("sector_surface.py", "sector_camera.py", "sector_fairing.py",
                                     "sector_evidence.py", "sector_patches.py", "sector_study.py")}


OpeningLoop = Annotated[list[tuple[FiniteFloat, FiniteFloat]], Field(min_length=3, max_length=64)]


class StudyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    controls: SurfaceControls = Field(default_factory=SurfaceControls)
    boundary: list[tuple[FiniteFloat, FiniteFloat]] | None = Field(default=None, min_length=12, max_length=128)
    openings: list[OpeningLoop] | None = Field(default=None, max_length=8)


class EvidenceBundleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int
    method: str = Field(min_length=3, max_length=240)
    annotations: dict
    holdout: dict


class CandidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int


def digest(value):
    return hashlib.sha256(value).hexdigest()


def routes(store):
    router = APIRouter()

    def context(project_id, request=None):
        project = store.project(project_id)
        if request and request.expected_revision != project["revision"]:
            raise HTTPException(409, "项目已变更，请重新载入辐条研究。")
        source = next((item for item in project["images"] if item["id"] == project["primary_image_id"]), None)
        if not source:
            raise HTTPException(422, "请先选择主参考图。")
        try:
            evidence, holdout, evidence_version, annotation_bytes, holdout_bytes = sector_evidence.load(
                store.root, source["sha256"], EVIDENCE_DIR)
        except FileNotFoundError:
            raise HTTPException(422, "当前主参考图尚无扇区证据。请先识别周期，再建立一组母扇区标注。")
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        original_path = store.root / "images" / f'{source["id"]}.source'
        display_path = store.root / "images" / f'{source["id"]}.jpg'
        if (not original_path.is_file() or digest(original_path.read_bytes()) != source["sha256"]
                or not display_path.is_file()):
            raise HTTPException(409, "原始图片缺失或摘要不一致，请先恢复原图。")
        if holdout["source_sha256"] != source["sha256"]:
            raise HTTPException(409, "验证标注与原图不匹配。")
        if request and request.boundary is not None:
            width, height = evidence["image_size"]
            if any(not (0 <= x <= width and 0 <= y <= height) for x, y in request.boundary):
                raise HTTPException(422, "边界点必须在原图范围内。")
            key = "panel_boundary" if request.controls.surface_scope == "root-panel" else "boundary"
            evidence["master"][key] = request.boundary
        if request and request.openings is not None:
            width, height = evidence["image_size"]
            if any(not (0 <= x <= width and 0 <= y <= height) for loop in request.openings for x, y in loop):
                raise HTTPException(422, "孔轮廓点必须在原图范围内。")
            evidence["uncertain_pockets"] = request.openings
        provenance = {"source_sha256": source["sha256"], "image_id": source["id"],
                      "annotation_sha256": digest(annotation_bytes), "holdout_sha256": digest(holdout_bytes),
                      "evidence_id": evidence_version["id"], "evidence_origin": evidence_version["origin"],
                      "effective_evidence_sha256": digest(json.dumps(evidence, sort_keys=True).encode()),
                      "project_id": project_id, "draft_revision": project["revision"], "algorithm": VERSION,
                      "implementation_sha256": IMPLEMENTATION_FILES["sector_surface.py"],
                      "implementation_files": IMPLEMENTATION_FILES,
                      "dependencies": {name: version(name) for name in ("numpy", "scipy")}}
        return project, evidence, holdout, display_path, provenance

    @router.put("/api/projects/{project_id}/sector-study/evidence", status_code=201)
    def save_evidence(project_id: str, body: EvidenceBundleRequest):
        project = store.project(project_id)
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "项目已变更，请重新载入后保存扇区证据。")
        source = next((item for item in project["images"] if item["id"] == project["primary_image_id"]), None)
        if not source:
            raise HTTPException(422, "请先选择主参考图。")
        source_path = store.root / "images" / f'{source["id"]}.source'
        display_path = store.root / "images" / f'{source["id"]}.jpg'
        try:
            source_bytes = source_path.read_bytes()
            if digest(source_bytes) != source["sha256"]:
                raise ValueError("原始图片缺失或摘要不一致，请先恢复原图。")
            with Image.open(display_path) as image:
                image_size = image.size
            sector_evidence.validate_bundle(body.annotations, body.holdout, source["sha256"], image_size)
            # Geometry validation is part of evidence acceptance, not deferred
            # until a later preview where a broken current pointer would persist.
            controls = SurfaceControls(surface_scope="root-panel" if body.annotations.get("uncertain_pockets") else "spoke")
            build_surface(body.annotations, controls, body.holdout)
            manifest = sector_evidence.save(store.root, source["sha256"], body.annotations, body.holdout, body.method)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return manifest

    @router.post("/api/projects/{project_id}/sector-study/evidence/candidate", status_code=201)
    def create_evidence_candidate(project_id: str, body: CandidateRequest):
        project = store.project(project_id)
        if project["revision"] != body.expected_revision:
            raise HTTPException(409, "项目已变更，请重新载入后生成母扇区候选。")
        source = next((item for item in project["images"] if item["id"] == project["primary_image_id"]), None)
        analysis = project.get("photo_analysis")
        if not source:
            raise HTTPException(422, "请先选择主参考图。")
        if not analysis or analysis.get("image_id") != source["id"]:
            raise HTTPException(422, "请先在照片对照页对当前主参考图执行自动识图。")
        source_path = store.root / "images" / f'{source["id"]}.source'
        display_path = store.root / "images" / f'{source["id"]}.jpg'
        try:
            source_bytes = source_path.read_bytes()
            if digest(source_bytes) != source["sha256"]:
                raise ValueError("原始图片缺失或摘要不一致，请先恢复原图。")
            with Image.open(display_path) as image:
                image_size = image.size
            evidence, holdout = sector_evidence.candidate_from_analysis(
                analysis, source["sha256"], image_size)
            sector_evidence.validate_bundle(evidence, holdout, source["sha256"], image_size)
            result = build_surface(evidence, SurfaceControls(surface_scope="spoke"), holdout)
            manifest = sector_evidence.save(
                store.root, source["sha256"], evidence, holdout, sector_evidence.CANDIDATE_ALGORITHM)
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {**manifest, "candidate": evidence["candidate"],
                "gate": result["gate"], "engineering_approved": False}

    @router.get("/api/projects/{project_id}/sector-study")
    def metadata(project_id: str):
        project, evidence, holdout, _, provenance = context(project_id)
        root = store.root / "sector-studies" / project_id
        runs = []
        if root.is_dir():
            for path in root.glob("*/manifest.json"):
                item = json.loads(path.read_text())
                runs.append({"id": item["id"], "created_at": item["created_at"], "controls": item["controls"],
                             "full_wheel_available": (path.parent / "full-wheel.glb").is_file()})
        default_controls = SurfaceControls(
            surface_scope="root-panel" if evidence.get("uncertain_pockets") else "spoke")
        return {"provenance": provenance, "evidence": evidence, "holdout": holdout,
                "controls": default_controls.model_dump(), "cad_groups": project["spec"]["spoke_count"],
                "cad_lugs": project["spec"]["bolt_count"], "evidence_scope": "per-image",
                "runs": sorted(runs, key=lambda row: row["created_at"], reverse=True)}

    @router.get("/api/projects/{project_id}/sector-study/source")
    def original(project_id: str):
        _, _, _, source_path, _ = context(project_id)
        return FileResponse(source_path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    def construct(project_id, request):
        _, evidence, holdout, _, provenance = context(project_id, request)
        try:
            result = build_surface(evidence, request.controls, holdout)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        result["provenance"] = provenance
        glb = to_glb(result)
        full_glb = to_full_wheel_glb(result)
        summary = {key: value for key, value in result.items() if key not in {"positions", "triangles", "boundary_xyz"}}
        summary["glb_sha256"] = digest(glb)
        summary["full_glb_sha256"] = digest(full_glb)
        return evidence, holdout, result, summary, glb, full_glb

    @router.post("/api/projects/{project_id}/sector-study/preview")
    def preview(project_id: str, body: StudyRequest):
        _, _, _, summary, glb, full_glb = construct(project_id, body)
        return {"report": summary, "glb_base64": base64.b64encode(glb).decode(),
                "full_glb_base64": base64.b64encode(full_glb).decode()}

    @router.post("/api/projects/{project_id}/sector-study/runs", status_code=201)
    def save_run(project_id: str, body: StudyRequest):
        evidence, holdout, result, summary, glb, full_glb = construct(project_id, body)
        run_id = uid()
        root = store.root / "sector-studies" / project_id
        staged = root / f".{run_id}.building"
        staged.mkdir(parents=True)
        files = {"surface.glb": glb, "full-wheel.glb": full_glb,
                 "report.json": json.dumps(summary, ensure_ascii=False, indent=2).encode(),
                 "mesh.json": json.dumps(result).encode(), "evidence.json": json.dumps(evidence, ensure_ascii=False, indent=2).encode(),
                 "holdout.json": json.dumps(holdout, ensure_ascii=False, indent=2).encode()}
        manifest = {"id": run_id, "created_at": now(), "controls": body.controls.model_dump(),
                    "provenance": summary["provenance"], "usage": "experimental_master_surface",
                    "engineering_approved": False,
                    "artifacts": {name: {"sha256": digest(content), "bytes": len(content)} for name, content in files.items()}}
        for name, content in files.items():
            (staged / name).write_bytes(content)
        (staged / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        # A concurrent image/project switch must not publish a result as current.
        with store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT revision FROM projects WHERE id=?", (project_id,)).fetchone()
            if not current or current["revision"] != body.expected_revision:
                raise HTTPException(409, "保存期间项目已变更；结果未发布，请重新载入。")
            os.rename(staged, root / run_id)
        return manifest

    @router.get("/api/projects/{project_id}/sector-study/runs/{run_id}/{artifact}")
    def download(project_id: str, run_id: str, artifact: str):
        store.project(project_id)
        names = {"glb": "surface.glb", "full-glb": "full-wheel.glb", "report": "report.json", "recipe": "manifest.json",
                 "evidence": "evidence.json", "holdout": "holdout.json", "mesh": "mesh.json"}
        if artifact not in names or not re.fullmatch(r"[a-f0-9]{32}", run_id):
            raise HTTPException(404, "研究文件不存在。")
        path = store.root / "sector-studies" / project_id / run_id / names[artifact]
        if not path.is_file():
            raise HTTPException(404, "研究文件不存在。")
        return FileResponse(path, filename=f"wheelcam-sector-{run_id[:8]}-{path.name}",
                            media_type="model/gltf-binary" if artifact == "glb" else "application/json")

    return router
