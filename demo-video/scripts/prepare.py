"""Copy only allowlisted demo evidence; never publish source asset directories."""
import hashlib
import json
import shutil
from pathlib import Path

root=Path(__file__).resolve().parents[2]
video=root/'demo-video'
public=video/'public';public.mkdir(exist_ok=True)
run=root/'runs/workbench-hardening-20260928/spark'
assets={
 'front.jpg':run/'reconstruct/style/photo.jpg',
 'before.png':run/'reconstruct/style/before_render.png',
 'after.png':run/'reconstruct/style/after_render.png',
 'overlay.png':run/'reconstruct/cad/front_overlay.png',
 'drawing.svg':run/'drawing.svg',
 'simulation.png':run/'simulation_3d.png',
 'workbench.png':root/'runs/workbench-hardening-20260928/workbench-quality.png',
}
manifest={}
for name,source in assets.items():
 if not source.is_file():raise SystemExit(f'Missing demo evidence: {source.relative_to(root)}')
 shutil.copyfile(source,public/name)
 manifest[name]={'source':str(source.relative_to(root)),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
evidence=json.loads((root/'docs/evidence/workbench-hardening-20260928.json').read_text())
# Narration contains these measured facts; stop if a replacement run contradicts them.
assert evidence['chain']['seconds']==57.7, 'Update narration and scene timing facts for the replacement run'
assert evidence['style_agent']['lip_pockets']['to']==15
assert evidence['visual_check']['front']['edge_mm']==2.18
(public/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False))
(video/'src/evidence.json').write_text(json.dumps(evidence,ensure_ascii=False))
(public/'asset-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print(f'Prepared {len(assets)} evidence assets; all local, no external image requests.')
