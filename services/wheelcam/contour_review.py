"""Independent photo marks versus the projected, exported CAD silhouette.

Measures only the annotated visible boundary, never a whole-wheel accuracy score.
"""
import hashlib
import json
import struct
from functools import lru_cache

import numpy as np
from fastapi import APIRouter, HTTPException
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field
from scipy.ndimage import binary_erosion, distance_transform_edt
from scipy.spatial.transform import Rotation

from .photo_pose import project
from .storage import now, uid


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    expected_revision: int = Field(ge=1)
    label: str = Field(min_length=1, max_length=80)
    points: list[tuple[float, float]] = Field(min_length=3, max_length=500)


def glb_triangles(blob):
    """Read our own uncompressed GLB exports, including node transforms and strides."""
    magic, version, length = struct.unpack_from('<4sII', blob)
    if magic != b'glTF' or version != 2 or length != len(blob):
        raise ValueError('实体 GLB 格式不受支持。')
    offset = 12; doc = None; binary = None
    while offset < length:
        size, kind = struct.unpack_from('<II', blob, offset)
        chunk = blob[offset+8:offset+8+size]; offset += size+8
        if kind == 0x4E4F534A:
            doc = json.loads(chunk)
        elif kind == 0x004E4942:
            binary = chunk
    if doc is None or binary is None:
        raise ValueError('实体 GLB 缺少网格。')

    def accessor(index):
        a = doc['accessors'][index]; v = doc['bufferViews'][a['bufferView']]
        if 'sparse' in a or v.get('buffer',0) != 0:
            raise ValueError('实体 GLB 使用了未支持的属性编码。')
        dtype = {5126:'<f4',5125:'<u4',5123:'<u2',5121:'u1'}[a['componentType']]
        width = {'VEC3':3,'SCALAR':1}[a['type']]; item = np.dtype(dtype).itemsize
        return np.ndarray((a['count'],width),dtype=dtype,buffer=binary,
                          offset=v.get('byteOffset',0)+a.get('byteOffset',0),
                          strides=(v.get('byteStride',width*item),item))

    def visit(index, parent):
        node = doc['nodes'][index]
        if 'matrix' in node:
            local = np.array(node['matrix']).reshape(4,4).T
        else:
            local = np.eye(4)
            local[:3,:3] = Rotation.from_quat(node.get('rotation',[0,0,0,1])).as_matrix() @ np.diag(node.get('scale',[1,1,1]))
            local[:3,3] = node.get('translation',[0,0,0])
        matrix = parent @ local
        if 'mesh' in node:
            for primitive in doc['meshes'][node['mesh']]['primitives']:
                if primitive.get('mode',4) != 4:
                    raise ValueError('实体 GLB 必须为三角网格。')
                p = accessor(primitive['attributes']['POSITION'])
                xyz = p @ matrix[:3,:3].T + matrix[:3,3]
                indices = accessor(primitive['indices']).reshape(-1) if 'indices' in primitive else np.arange(len(p))
                yield xyz[indices.reshape(-1,3)]
        for child in node.get('children',[]):
            yield from visit(child,matrix)

    restore = np.eye(4); restore[:3,:3] = Rotation.from_euler('x',90,degrees=True).as_matrix()
    triangles = list(t for root in doc['scenes'][doc.get('scene',0)]['nodes'] for t in visit(root,restore))
    if not triangles:
        raise ValueError('实体没有可评估的三角形。')
    return np.concatenate(triangles)


@lru_cache(maxsize=3)
def silhouette(blob, pose_json, size):
    triangles = glb_triangles(blob)
    pose = json.loads(pose_json)
    projected = project(triangles, pose)
    if not np.isfinite(projected).all():
        raise ValueError('当前相机不能投影实体。')
    canvas = Image.new('1',size)
    draw = ImageDraw.Draw(canvas)
    for triangle in projected:
        draw.polygon([tuple(p) for p in triangle], fill=1)
    mask = np.asarray(canvas, dtype=bool)
    boundary = mask & ~binary_erosion(mask)
    # Image clipping is not a physical model edge.
    boundary[[0,-1],:] = False; boundary[:,[0,-1]] = False
    if not boundary.any():
        raise ValueError('实体未落在参考图范围内。')
    return boundary


def measure(boundary, points):
    p = np.array(points, dtype=float); samples = []
    for a,b in zip(p[:-1],p[1:]):
        length = np.linalg.norm(b-a)
        if length > 0:
            samples.extend(a+(b-a)*t for t in np.linspace(0,1,max(2,int(np.ceil(length))+1)))
    if not samples:
        raise ValueError('请标注有长度的连续轮廓。')
    samples = np.array(samples); pixels = np.rint(samples).astype(int)
    distances = distance_transform_edt(~boundary)[pixels[:,1],pixels[:,0]]
    return dict(mean_px=round(float(np.mean(distances)),3), p95_px=round(float(np.percentile(distances,95)),3),
                within_3px=round(float(np.mean(distances<=3)),4), sample_count=len(samples),
                samples=[{'point':p.tolist(),'distance_px':round(float(d),2)} for p,d in zip(samples,distances)])


def routes(store):
    router = APIRouter()

    @router.get('/api/projects/{project_id}/contour-reviews')
    def listing(project_id: str):
        store.project(project_id)
        with store.connection() as db:
            return [json.loads(r['record']) for r in db.execute('SELECT record FROM contour_evaluations WHERE project_id=? ORDER BY rowid DESC', (project_id,))]

    @router.post('/api/projects/{project_id}/builds/{job_id}/contour-reviews', status_code=201)
    def evaluate(project_id: str, job_id: str, body: ReviewRequest):
        current = store.project(project_id)
        if current['revision'] != body.expected_revision:
            raise HTTPException(409,'项目已改变，请刷新后标注。')
        job = next((j for j in current['jobs'] if j['id']==job_id and j['status']=='succeeded'),None)
        if not job:
            raise HTTPException(404,'成功模型版本不存在。')
        analysis = job['snapshot'].get('photo_analysis')
        if not analysis or not analysis.get('camera_fit'):
            raise HTTPException(422,'该版本未保存相机拟合；请识图、应用并生成版本后评估。')
        if analysis['image_id'] != current['primary_image_id']:
            raise HTTPException(409,'当前主图与模型的相机参考图不同，请切回原图。')
        w,h = analysis['image_size']
        if not (1 < w <= 1024 and 1 < h <= 1024) or any(not (0<=x<w and 0<=y<h) for x,y in body.points):
            raise HTTPException(422,'轮廓点超出检测图范围。')
        path = store.root/'models'/job_id/'wheel.glb'
        if not path.is_file():
            raise HTTPException(422,'实体文件缺失。')
        blob = path.read_bytes()
        try:
            boundary = silhouette(blob,json.dumps(analysis['camera_fit']['pose'],sort_keys=True),(w,h))
            metrics = measure(boundary,body.points)
        except (ValueError, KeyError, IndexError, struct.error) as exc:
            raise HTTPException(422,str(exc)) from exc
        result = dict(id=uid(), project_id=project_id, job_id=job_id, image_id=analysis['image_id'],
                      image_sha256=analysis['image_sha256'], model_sha256=hashlib.sha256(blob).hexdigest(),
                      analysis_id=analysis['id'], pose=analysis['camera_fit']['pose'], image_size=[w,h],
                      label=body.label, points=body.points, metrics=metrics, created_at=now(),
                      annotation_source='manual_independent', algorithm='projected-glb-boundary-v1',
                      scope='annotated_boundary_only',
                      note='人工标注到实体投影遮挡边界的单向像素距离；不覆盖未标区域、表面棱线或实物尺寸。相机与深度沿用该版本假设。')
        with store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            fresh = db.execute('SELECT revision,primary_image_id FROM projects WHERE id=?',(project_id,)).fetchone()
            if not fresh or fresh['revision'] != body.expected_revision or fresh['primary_image_id'] != analysis['image_id']:
                raise HTTPException(409,'评估期间项目已改变，请重新标注。')
            db.execute('INSERT INTO contour_evaluations VALUES(?,?,?)',(result['id'],project_id,json.dumps(result,ensure_ascii=False)))
        return result

    @router.get('/api/projects/{project_id}/contour-reviews/{review_id}')
    def detail(project_id: str, review_id: str):
        with store.connection() as db:
            row = db.execute('SELECT record FROM contour_evaluations WHERE id=? AND project_id=?',(review_id,project_id)).fetchone()
            if not row:
                raise HTTPException(404,'轮廓评估不存在。')
            return json.loads(row['record'])

    return router
