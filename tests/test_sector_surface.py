import copy
import io
import json
import math
from pathlib import Path
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from wheelcam.sector_surface import (SurfaceControls, build_surface, project, surface_triangulation,
                                     to_full_wheel_glb, to_glb)
from wheelcam.sector_fairing import attachment_edges
from wheelcam import sector_study
from wheelcam.app import create_app

EVIDENCE = Path(__file__).resolve().parents[1] / "experiments/sector-study"


@pytest.fixture(scope="module")
def evidence():
    return json.loads((EVIDENCE / "annotations.json").read_text())


@pytest.fixture(scope="module")
def surface(evidence):
    return build_surface(evidence, SurfaceControls(), json.loads((EVIDENCE / "holdout.json").read_text()))


def test_concave_triangulation_preserves_area_and_opening():
    points = np.array([[0, 0], [4, 0], [4, 4], [3, 4], [3, 1], [1, 1], [1, 4], [0, 4]], float)
    boundary, points, faces = surface_triangulation(points, spacing=.5)
    a = points[faces[:, 1]]-points[faces[:, 0]]
    b = points[faces[:, 2]]-points[faces[:, 0]]
    assert np.sum(abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]))/2 == pytest.approx(10)
    edges = {tuple(sorted((int(a), int(b)))) for f in faces for a, b in zip(f, np.roll(f, -1))}
    assert all(tuple(sorted((i, (i+1) % len(boundary)))) in edges for i in range(len(boundary)))


def test_closed_surface_and_normals_do_not_turn_into_engineering_approval(surface):
    assert surface["groups"] == 6 and surface["lug_count"] == 6
    assert surface["integrity"]["nonmanifold_edges"] == 0
    assert surface["integrity"]["min_axial_thickness"] > 0
    p, f = np.array(surface["positions"]), np.array(surface["triangles"])
    volume = np.einsum("ij,ij->i", p[f[:, 0]], np.cross(p[f[:, 1]], p[f[:, 2]])).sum()/6
    assert volume > 0
    assert np.linalg.norm(np.cross(p[f[:, 1]]-p[f[:, 0]], p[f[:, 2]]-p[f[:, 0]]), axis=1).min() > 1e-12
    assert surface["gate"]["passed"] is False
    assert surface["gate"]["full_wheel_generated"] is False
    assert surface["engineering_approved"] is False


def test_holdout_cannot_change_camera_geometry_or_relief(evidence, surface):
    poisoned = {"points": [[0, 0], [620, 591]], "group_rotation_deg": -60, "reviewed": True}
    other = build_surface(evidence, SurfaceControls(), poisoned)
    assert other["camera"] == surface["camera"]
    assert other["positions"] == surface["positions"]
    assert other["triangles"] == surface["triangles"]
    assert other["holdout"]["max_px"] != surface["holdout"]["max_px"]
    assert other["gate"]["passed"] is False


def test_new_relief_preserves_boundary_and_optional_perspective_is_consistent(evidence, surface):
    # Capped v1/v2 profiles still preserve the full source boundary. Continuous
    # cuts intentionally permit axial displacement there, tested separately.
    old = build_surface(evidence, SurfaceControls(relief_profile="legacy", junction_mode="capped"))
    capped = build_surface(evidence, SurfaceControls(junction_mode="capped"))
    np.testing.assert_allclose(old["boundary_xyz"], capped["boundary_xyz"], atol=1e-12)
    assert np.max(abs(np.array(old["positions"])-surface["positions"])) > .001
    candidate = build_surface(evidence, SurfaceControls(camera_model="anchors", junction_mode="capped"))
    assert candidate["camera"]["distance_radii"] < 1e5
    assert candidate["integrity"]["nonmanifold_edges"] == 0
    np.testing.assert_allclose(project(candidate["boundary_xyz"], candidate["camera"]), candidate["boundary"], atol=1e-7)


def test_ridges_and_grooves_change_real_geometry_not_only_material(evidence, surface):
    flat = build_surface(evidence, SurfaceControls(crown=0, ridge=0, groove=0))
    a, b = np.array(flat["positions"]), np.array(surface["positions"])
    np.testing.assert_array_equal(a[:, :2], b[:, :2])
    assert (b[:, 2]-a[:, 2]).max() > .007
    assert np.max(abs(project(a, flat["camera"])-project(b, flat["camera"]))) > .5
    # Only cut interiors are allowed to move; exposed edges stay on the original
    # boundary. This software invariant is not an independent accuracy score.
    cuts = attachment_edges(surface["boundary"], [evidence["master"]["root_seam"], *evidence["master"]["tip_seams"]])
    cut_interior = cuts & np.roll(cuts, 1)
    projected = project(surface["boundary_xyz"], surface["camera"])
    np.testing.assert_allclose(projected[~cut_interior], np.array(surface["boundary"])[~cut_interior], atol=1e-7)
    assert np.max(abs(projected[cut_interior]-np.array(surface["boundary"])[cut_interior])) > .5
    grooved = build_surface(evidence, SurfaceControls(groove=.01))
    assert np.min(np.array(grooved["positions"])[:, 2]-b[:, 2]) < -.003


def test_v8_section_cage_changes_branch_cross_sections_without_breaking_shell(evidence, surface):
    candidate = build_surface(evidence, SurfaceControls(surface_model="section-cage-v8"))
    assert candidate["algorithm"] == "master-surface-v8"
    assert candidate["surface_model"] == "section-cage-v8"
    assert candidate["section_cage"]["branch_count"] == 2
    assert candidate["section_cage"]["section_order"] == ["edge", "ridge", "body", "groove", "edge"]
    assert all(branch["stations"] == 48 and branch["median_half_width_px"] > 5
               for branch in candidate["section_cage"]["branches"])
    assert candidate["section_cage"]["review_required"] is True
    assert candidate["section_cage"]["narrow_branches"] == [1]
    legacy = np.asarray(surface["positions"])
    changed = np.asarray(candidate["positions"])
    np.testing.assert_array_equal(legacy[:, :2], changed[:, :2])
    assert np.max(abs(legacy[:, 2]-changed[:, 2])) > .002
    assert candidate["integrity"]["nonmanifold_edges"] == 0
    assert candidate["integrity"]["connected_components"] == 1
    assert candidate["integrity"]["min_axial_thickness"] > 0


def test_v8_requires_paired_semantic_guides(evidence):
    invalid = copy.deepcopy(evidence)
    invalid["master"]["groove_guides"] = []
    with pytest.raises(ValueError, match="成对"):
        build_surface(invalid, SurfaceControls(surface_model="section-cage-v8"))


def test_v10_uses_boundary_intervals_and_separate_root_patch(evidence, surface):
    candidate = build_surface(evidence, SurfaceControls(surface_model="boundary-patches-v10"))
    report = candidate["section_cage"]
    assert candidate["algorithm"] == "master-surface-v10"
    assert report["algorithm"] == "boundary-section-patches-v2"
    assert report["branch_count"] == 2
    assert report["review_required"] is False
    assert report["root_patch"]["independent"] is True
    assert report["root_patch"]["blend_radius_px"] > 50
    assert all(branch["width_source"] == "panel-boundary-intersections" for branch in report["branches"])
    assert all(.35 < branch["entry_station"] < .55 for branch in report["branches"])
    assert all(branch["min_total_width_px"] > 4 for branch in report["branches"])
    # The close lower ridge/groove pair must not collapse the whole branch.
    assert report["branches"][1]["median_total_width_px"] > 3*report["branches"][1]["median_guide_separation_px"]
    assert np.max(abs(np.asarray(candidate["positions"])[:, 2]-np.asarray(surface["positions"])[:, 2])) > .002
    assert candidate["integrity"]["nonmanifold_edges"] == 0
    assert candidate["integrity"]["connected_components"] == 1


def test_v10_requires_paired_semantic_guides(evidence):
    invalid = copy.deepcopy(evidence)
    invalid["master"]["ridge_guides"] = []
    with pytest.raises(ValueError, match="成对"):
        build_surface(invalid, SurfaceControls(surface_model="boundary-patches-v10"))


def test_curve_edit_changes_surface_and_rejects_degenerate_boundary(evidence, surface):
    changed = copy.deepcopy(evidence)
    changed["master"]["panel_boundary"][3][1] -= 1
    other = build_surface(changed, SurfaceControls())
    assert other["boundary"] != surface["boundary"]
    changed["master"]["panel_boundary"] = [[1, 1]]*12
    with pytest.raises(ValueError):
        build_surface(changed, SurfaceControls())


@pytest.mark.parametrize("value", [{"ridge": float("nan")}, {"groove": .1}, {"thickness": 0}, {"extra": 4},
                                  {"fairing_px": -1}, {"fairing_px": 3.1}, {"junction_mode": "guess"}])
def test_controls_reject_invalid_or_unrecognized_values(value):
    with pytest.raises(ValidationError):
        SurfaceControls(**value)


def test_glb_contains_matching_finite_geometry_and_explicit_units(surface):
    binary = to_glb(surface)
    magic, version, total = struct.unpack_from("<III", binary)
    assert (magic, version, total) == (0x46546C67, 2, len(binary))
    size, kind = struct.unpack_from("<II", binary, 12)
    assert kind == 0x4E4F534A
    document = json.loads(binary[20:20+size])
    assert document["extras"]["engineering_approved"] is False
    assert document["accessors"][2]["count"] == len(surface["triangles"])*3
    start = 28+size
    xyz = np.frombuffer(binary, dtype="<f4", count=document["accessors"][0]["count"]*3, offset=start).reshape(-1, 3)
    faces = np.frombuffer(binary, dtype="<u4", count=document["accessors"][2]["count"],
                          offset=start+document["bufferViews"][2]["byteOffset"]).reshape(-1, 3)
    expected = np.array(surface["positions"])[:, [0, 2, 1]]
    expected[:, 2] *= -1
    np.testing.assert_allclose(xyz[faces], expected[np.array(surface["triangles"])], atol=1e-7)


def test_full_wheel_preview_instances_six_sectors_and_labels_assumed_context(evidence):
    surface = build_surface(evidence, SurfaceControls(surface_model="section-cage-v8"))
    binary = to_full_wheel_glb(surface)
    _, _, total = struct.unpack_from("<III", binary)
    size, kind = struct.unpack_from("<II", binary, 12)
    document = json.loads(binary[20:20+size])
    assert kind == 0x4E4F534A and total == len(binary)
    assert len(document["meshes"]) == 3
    assert len([node for node in document["nodes"] if node.get("mesh") == 0]) == 6
    assert len([node for node in document["nodes"] if node.get("mesh") == 2]) == 6
    assert document["extras"]["algorithm"] == "sixfold-preview-v1"
    assert document["extras"]["connected_or_fused"] is False
    assert document["extras"]["rim_and_hub_are_assumed_context"] is True
    assert document["extras"]["display_radial_remap"]["target_tip_R"] == .84
    assert document["extras"]["display_radial_remap"]["affects_local_validation"] is False
    assert surface["gate"]["full_wheel_generated"] is False
    assert surface["full_wheel_preview"]["generated"] is True
    assert surface["engineering_approved"] is False


@pytest.fixture
def study_client(tmp_path, monkeypatch, evidence):
    directory = tmp_path / "evidence"
    directory.mkdir()
    photo = io.BytesIO()
    Image.new("RGB", (620, 591), "white").save(photo, "JPEG")
    data = copy.deepcopy(evidence)
    data["source_sha256"] = sector_study.digest(photo.getvalue())
    holdout = json.loads((EVIDENCE / "holdout.json").read_text())
    holdout["source_sha256"] = data["source_sha256"]
    (directory / "annotations.json").write_text(json.dumps(data))
    (directory / "holdout.json").write_text(json.dumps(holdout))
    monkeypatch.setattr(sector_study, "EVIDENCE_DIR", directory)
    with TestClient(create_app(tmp_path / "workspace", start_worker=False)) as client:
        created = client.post("/api/projects", json={"name": "study"}).json()
        project = client.post(f'/api/projects/{created["id"]}/images', files={"file": ("ref.jpg", photo.getvalue(), "image/jpeg")}).json()
        yield client, project


def test_study_preview_save_and_download_preserve_cad_and_bind_provenance(study_client):
    client, original = study_client
    url = f'/api/projects/{original["id"]}/sector-study'
    body = {"expected_revision": original["revision"], "controls": {"ridge": .012}}
    assert client.get(url).status_code == 200
    assert client.get(url+"/source").status_code == 200
    preview = client.post(url+"/preview", json=body)
    assert preview.status_code == 200
    assert preview.json()["full_glb_base64"]
    assert preview.json()["report"]["full_wheel_preview"]["connected_or_fused"] is False
    saved = client.post(url+"/runs", json=body)
    assert saved.status_code == 201
    manifest = saved.json()
    assert manifest["usage"] == "experimental_master_surface"
    assert manifest["artifacts"]["surface.glb"]["sha256"] == preview.json()["report"]["glb_sha256"]
    assert manifest["artifacts"]["full-wheel.glb"]["sha256"] == preview.json()["report"]["full_glb_sha256"]
    assert manifest["provenance"]["holdout_sha256"]
    assert set(manifest["provenance"]["implementation_files"]) == {
        "sector_surface.py", "sector_camera.py", "sector_fairing.py", "sector_evidence.py",
        "sector_patches.py", "sector_study.py"
    }
    glb = client.get(url+f'/runs/{manifest["id"]}/glb')
    assert sector_study.digest(glb.content) == manifest["artifacts"]["surface.glb"]["sha256"]
    full_glb = client.get(url+f'/runs/{manifest["id"]}/full-glb')
    assert sector_study.digest(full_glb.content) == manifest["artifacts"]["full-wheel.glb"]["sha256"]
    assert client.get(url+f'/runs/{manifest["id"]}/step').status_code == 404
    assert client.get(f'/api/projects/{original["id"]}').json() == original
    assert client.get(url).json()["runs"][0]["id"] == manifest["id"]


def test_study_rejects_stale_revision_wrong_image_and_invalid_curve(study_client):
    client, project = study_client
    url = f'/api/projects/{project["id"]}/sector-study'
    assert client.post(url+"/preview", json={"expected_revision": 0}).status_code == 409
    assert client.post(url+"/preview", json={"expected_revision": project["revision"], "boundary": [[-10, 20]]*12}).status_code == 422
    assert client.post(url+"/preview", json={"expected_revision": project["revision"], "boundary": [[1, 1]]*12}).status_code == 422
    other = client.post("/api/projects", json={"name": "other"}).json()
    assert client.get(f'/api/projects/{other["id"]}/sector-study').status_code == 422


def test_source_tampering_is_not_presented_as_a_valid_study(study_client):
    client, project = study_client
    store = client.app.state.store
    (store.root / "images" / f'{project["primary_image_id"]}.source').write_bytes(b"changed")
    assert client.get(f'/api/projects/{project["id"]}/sector-study').status_code == 409


def test_opening_edits_are_validated_saved_and_do_not_touch_the_cad(study_client, evidence):
    client, original = study_client
    url = f'/api/projects/{original["id"]}/sector-study'
    loops = copy.deepcopy(evidence["uncertain_pockets"])
    loops[0][0][0] += .5
    body = {"expected_revision": original["revision"], "openings": loops}
    preview = client.post(url+"/preview", json=body)
    assert preview.status_code == 200
    assert preview.json()["report"]["openings"]["count"] == 2
    run = client.post(url+"/runs", json=body)
    assert run.status_code == 201
    persisted = client.get(url+f'/runs/{run.json()["id"]}/evidence').json()
    assert persisted["uncertain_pockets"] == loops
    for invalid in ([[[1, 1], [2, 1]]], [[[-1, 2], [2, 2], [2, 3]]], [[[0, 0], [2, 0], [2, 3]]]):
        assert client.post(url+"/preview", json={**body, "openings": invalid}).status_code == 422
    assert client.get(f'/api/projects/{original["id"]}').json() == original


def test_each_source_can_save_versioned_evidence_without_sample_sha(study_client, evidence):
    client, _ = study_client
    created = client.post("/api/projects", json={"name": "new source"}).json()
    photo = io.BytesIO()
    Image.new("RGB", (620, 591), "#dddddd").save(photo, "JPEG")
    project = client.post(
        f'/api/projects/{created["id"]}/images',
        files={"file": ("other.jpg", photo.getvalue(), "image/jpeg")},
    ).json()
    url = f'/api/projects/{project["id"]}/sector-study'
    assert client.get(url).status_code == 422

    annotations = copy.deepcopy(evidence)
    annotations["source_sha256"] = sector_study.digest(photo.getvalue())
    holdout = json.loads((EVIDENCE / "holdout.json").read_text())
    holdout["source_sha256"] = annotations["source_sha256"]
    payload = {"expected_revision": project["revision"], "method": "manual-test",
               "annotations": annotations, "holdout": holdout}
    first = client.put(url+"/evidence", json=payload)
    assert first.status_code == 201, first.text
    first_id = first.json()["id"]
    metadata = client.get(url)
    assert metadata.status_code == 200
    assert metadata.json()["provenance"]["evidence_origin"] == "per-image"
    assert metadata.json()["provenance"]["evidence_id"] == first_id

    second = client.put(url+"/evidence", json={**payload, "method": "manual-test-v2"})
    assert second.status_code == 201, second.text
    second_id = second.json()["id"]
    assert second_id != first_id
    root = client.app.state.store.root / "sector-evidence" / annotations["source_sha256"]
    assert (root / f"{first_id}.manifest.json").is_file()
    assert (root / f"{second_id}.manifest.json").is_file()
    assert json.loads((root / "current.json").read_text())["id"] == second_id

    wrong = copy.deepcopy(payload)
    wrong["annotations"] = {**annotations, "source_sha256": "0"*64}
    assert client.put(url+"/evidence", json=wrong).status_code == 422
    assert client.put(url+"/evidence", json={**payload, "expected_revision": 0}).status_code == 409


def test_detected_blade_traces_create_editable_scaled_sector_candidate(tmp_path):
    with TestClient(create_app(tmp_path / "workspace", start_worker=False)) as client:
        created = client.post("/api/projects", json={"name": "automatic sector", "preset": "photo-paired-refined"}).json()
        photo = io.BytesIO()
        Image.new("RGB", (1440, 1280), "#dddddd").save(photo, "PNG")
        project = client.post(
            f'/api/projects/{created["id"]}/images',
            files={"file": ("large.png", photo.getvalue(), "image/png")},
        ).json()
        groups, cx, cy, radius = 6, 360, 320, 250
        traces = []
        for group in range(groups):
            theta = math.radians(group*360/groups)
            for side in (-1, 1):
                samples = []
                for radial in np.linspace(.86, .36, 21):
                    center, half = side*.08, .014
                    points = [[cx+radius*(radial*math.cos(theta)-u*math.sin(theta)),
                               cy+radius*(radial*math.sin(theta)+u*math.cos(theta))]
                              for u in (center-half, center+half)]
                    samples.append({"radius_ratio": float(radial), "accepted": True, "points": points})
                traces.append({"group": group, "side": side, "samples": samples})
        analysis = {"id": "a"*32, "image_id": project["primary_image_id"],
                    "image_sha256": sector_study.digest(photo.getvalue()), "image_size": [720, 640],
                    "status": "candidates", "spokes": {"groups": groups, "image_phase_deg": 0},
                    "ellipse": {"cx": cx, "cy": cy, "rx": radius, "ry": radius},
                    "outer_points": [[cx+radius*math.cos(t), cy+radius*math.sin(t)]
                                     for t in np.linspace(0, 2*math.pi, 24, endpoint=False)],
                    "traces": traces, "created_at": "2026-09-21T00:00:00+00:00"}
        with client.app.state.store.connection() as db:
            db.execute("INSERT INTO image_analyses VALUES(?,?,?,?,?)",
                       (analysis["id"], project["id"], project["primary_image_id"], json.dumps(analysis), analysis["created_at"]))
        url = f'/api/projects/{project["id"]}/sector-study'
        response = client.post(url+"/evidence/candidate", json={"expected_revision": project["revision"]})
        assert response.status_code == 201, response.text
        assert response.json()["candidate"]["eligible_groups"] == list(range(groups))
        assert response.json()["engineering_approved"] is False
        metadata = client.get(url).json()
        assert metadata["evidence"]["image_size"] == [1440, 1280]
        assert metadata["provenance"]["evidence_origin"] == "per-image"
        assert len(metadata["evidence"]["master"]["boundary"]) == 48
        assert metadata["evidence"]["uncertain_pockets"] == []
        assert max(point[0] for point in metadata["evidence"]["rim_points"]) > 1200
        assert client.post(url+"/preview", json={"expected_revision": project["revision"],
                                                  "controls": {"surface_scope": "spoke"}}).status_code == 200
