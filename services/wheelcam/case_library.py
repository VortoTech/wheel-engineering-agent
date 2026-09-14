"""Versioned visual examples; retrieval scores are preferences, not image accuracy."""
import hashlib
import json
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .models import BuildRequest, WheelSpec, TEMPLATE_VERSION
from .presets import preset_spec
from .storage import now, uid

# Visible styling only. Mounting geometry, axial dimensions, material and preparation stay local.
STYLE_KEYS = {'spoke_style', 'spoke_count', 'spoke_phase_deg', 'sweep_deg',
              'spoke_width_hub_mm', 'spoke_width_rim_mm',
              'paired_blade_root_mm', 'paired_window_root_mm', 'paired_window_blend_mm',
              'paired_root_round_mm', 'paired_gap_flare_mm', 'paired_gap_mm',
              'paired_tip_width_mm', 'paired_split_start_mm', 'paired_shoulder_mm',
              'paired_mid_mm', 'paired_tip_inset_mm', 'lip_extension_mm',
              'spoke_method', 'window_outlines_mm', 'window_edge_fillet_mm'}
# Edge roundings are process choices, not proportions: they are carried over without scaling.
UNSCALED = {'window_edge_fillet_mm'}


def _scaled(key, value, factor):
    if key == 'window_outlines_mm':
        return [[(round(x*factor, 3), round(y*factor, 3)) for x, y in outline] for outline in value]
    return round(value*factor, 4) if key.endswith('_mm') and key not in UNSCALED else value


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    job_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    note: str = Field('', max_length=1000)
    role: Literal['reference', 'evaluation'] = 'reference'


def install(store):
    with store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS visual_cases (id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id), record TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS contour_evaluations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), record TEXT NOT NULL)')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(projects)')}
        if 'case_selection' not in columns:
            db.execute('ALTER TABLE projects ADD COLUMN case_selection TEXT')


def candidates(store):
    entries = []
    for name, label in [('single','单辐放样模板'), ('photo-paired-8','双辐放样模板'), ('photo-paired-refined','双辐外伸模板')]:
        spec, _ = preset_spec(name)
        entries.append(dict(id='template:'+name, name=label, spec=spec.model_dump(), role='reference',
                            kind='template', template_version=TEMPLATE_VERSION, validation='template_assumptions', image_id=None))
    with store.connection() as db:
        entries += [json.loads(r['record']) for r in db.execute('SELECT record FROM visual_cases ORDER BY id')]
    return entries


def transfer(case, spec):
    factor = (spec['rim_diameter_in']*25.4+35)/(case['spec']['rim_diameter_in']*25.4+35)
    changes = {k: _scaled(k, v, factor) for k, v in case['spec'].items() if k in STYLE_KEYS}
    if changes.get('spoke_method', 'loft') == 'loft':
        # A lofted case keeps the draft's fitted windows, so switching back stays possible.
        changes.pop('window_outlines_mm', None); changes.pop('window_edge_fillet_mm', None)
    try:
        result = json.loads(WheelSpec(**{**spec, **changes}).model_dump_json())
    except ValidationError:
        return None, '按目标外径换算后超出当前模板约束，请调整参数或选择其他候选。'
    return {k: v for k, v in result.items() if k in STYLE_KEYS and v != spec.get(k)}, None


def routes(store):
    router = APIRouter()

    @router.get('/api/cases')
    def listing():
        return candidates(store)

    @router.post('/api/projects/{project_id}/cases', status_code=201)
    def save_case(project_id: str, body: CaseCreate):
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=? AND project_id=?', (body.job_id,project_id)).fetchone()
            if not row:
                raise HTTPException(404, '模型版本不属于此项目。')
            if row['status'] != 'succeeded':
                raise HTTPException(422, '只能收录成功生成的模型版本。')
            old = db.execute('SELECT record FROM visual_cases WHERE job_id=?', (body.job_id,)).fetchone()
            if old:
                raise HTTPException(409, '此模型版本已经收录，案例不可覆盖。')
            job = store.job_dict(row); snapshot = job['snapshot']
            image = next((i for i in snapshot['reference_images'] if i['id'] == snapshot['primary_image_id']), None)
            model = store.root/'models'/body.job_id/'wheel.glb'
            if not image or not model.is_file():
                raise HTTPException(422, '该版本需要绑定参考图和实体 GLB 文件。')
            record = dict(id=uid(), kind='case', name=body.name.strip() or '未命名案例', note=body.note,
                          role=body.role, job_id=body.job_id, project_id=project_id,
                          image_id=image['id'], image_sha256=image['sha256'],
                          model_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),
                          spec=snapshot['spec'], sources=snapshot['sources'],
                          template_version=snapshot['template_version'], created_at=now(),
                          validation='geometry_only', engineering_verified=False,
                          limitations=job['report'].get('limitations', []))
            db.execute('INSERT INTO visual_cases VALUES(?,?,?)', (record['id'], body.job_id, json.dumps(record, ensure_ascii=False)))
        return record

    @router.get('/api/projects/{project_id}/case-candidates')
    def match(project_id: str):
        project = store.project(project_id); spec = project['spec']
        ranked = []
        entries = candidates(store)
        held_images = {c.get("image_sha256") for c in entries if c["role"] == "evaluation"}
        for case in entries:
            if case['role'] != 'reference' or (case.get('image_sha256') and case['image_sha256'] in held_images):
                continue
            method = lambda s: s.get('spoke_method', 'loft')
            same = method(case['spec']) == method(spec) and (method(spec) == 'window' or case['spec']['spoke_style'] == spec['spoke_style'])
            count_delta = abs(case['spec']['spoke_count']-spec['spoke_count'])
            changes, error = transfer(case, spec)
            ranked.append({**case, 'rank_score': (60 if same else 0)+max(0,40-10*count_delta),
                           'reasons': [('辐条类型一致' if same else '辐条类型不同'), f"组数差 {count_delta}"],
                           'changes': changes, 'blocked_reason': error,
                           'same_image': any(i.get('sha256') == case.get('image_sha256') for i in project['images'])})
        return {'base_revision': project['revision'], 'basis': '当前草稿的辐条类型与组数；未使用图像语义分类',
                'candidates': sorted(ranked, key=lambda c:(-c['rank_score'],c['id']))}

    @router.post('/api/projects/{project_id}/cases/{case_id}/apply')
    def apply(project_id: str, case_id: str, body: BuildRequest):
        case = next((c for c in candidates(store) if c['id'] == case_id and c['role']=='reference'), None)
        held_images = {c.get('image_sha256') for c in candidates(store) if c['role'] == 'evaluation'}
        if not case or (case.get('image_sha256') and case['image_sha256'] in held_images):
            raise HTTPException(404, '可复用案例不存在；评估保留集不能用于套用。')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            if not row:
                raise HTTPException(404, '项目不存在。')
            if row['revision'] != body.expected_revision:
                raise HTTPException(409, '草稿已改变，请刷新候选后重试。')
            original = json.loads(row['spec']); changes, error = transfer(case, original)
            if error:
                raise HTTPException(422, error)
            sources = json.loads(row['sources'])
            for k in changes:
                sources[k] = {'kind':'manual', 'note':f"人工选择案例 {case_id}；按目标外径换算造型，非实测"}
            selection = dict(case_id=case_id, template_version=case['template_version'], source_job_id=case.get('job_id'),
                             applied_revision=row['revision']+1, changes=changes, created_at=now())
            db.execute('UPDATE projects SET spec=?,sources=?,case_selection=?,applied_analysis_id=NULL,revision=revision+1,updated_at=? WHERE id=?',
                       (json.dumps({**original, **changes}),json.dumps(sources,ensure_ascii=False),json.dumps(selection,ensure_ascii=False),now(),project_id))
        return store.project(project_id)

    return router
