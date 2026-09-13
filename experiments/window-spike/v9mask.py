import json, sqlite3, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'services'))
from wheelcam.contour_review import glb_triangles
from wheelcam.photo_pose import project
S = '.'; job = sys.argv[1] if len(sys.argv) > 1 else '7936b2508857492ab2ff35d564a86501'
db = sqlite3.connect(ROOT/'data'/'wheelcam.sqlite3')
snap = json.loads(db.execute('select snapshot from jobs where id=?', (job,)).fetchone()[0])
a = snap['photo_analysis']; w, h = a['image_size']
im = Image.open(f"{ROOT}/data/images/{a['image_id']}.source").convert('RGB'); im.thumbnail((720, 720))
assert im.size == (w, h), im.size
im.save(f'{S}/photo720.png')
tri = project(glb_triangles(open(f'{ROOT}/data/models/{job}/wheel.glb','rb').read()), a['camera_fit']['pose'])
canvas = Image.new('L', (w, h)); d = ImageDraw.Draw(canvas)
for t in tri: d.polygon([tuple(p) for p in t], fill=255)
canvas.save(f'{S}/v9_mask.png')
json.dump({'pose': a['camera_fit']['pose'], 'ellipse': a['ellipse'], 'spec': snap['spec'], 'image_size': [w, h]}, open(f'{S}/context.json', 'w'))
print('ok', w, h, (np.asarray(canvas) > 0).mean())
