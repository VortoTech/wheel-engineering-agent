"""Create a code-native SVG/HTML study; keep the original photo unchanged and local."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('photo', type=Path)
parser.add_argument('cad_front_svg', type=Path)
parser.add_argument('--output', type=Path, default=Path('artifacts/paired/study'))
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
shutil.copyfile(args.photo, args.output / 'reference.jpg')
shutil.copyfile(args.cad_front_svg, args.output / 'cad-front.svg')
groups = [(875,96),(1308,240),(1505,641),(1351,1050),(966,1252),(572,1122),(347,735),(477,314)]
metadata = {'source_sha256': hashlib.sha256(args.photo.read_bytes()).hexdigest(),
            'coordinate_frame_px': [1824,1368], 'method': 'manual approximate pixel observations; no metric calibration',
            'outer_ellipse': {'cx':923,'cy':668,'rx':653,'ry':646}, 'group_tip_centres': groups}
(args.output / 'landmarks.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
markers = ''.join(f'<circle cx="{x}" cy="{y}" r="16"/><text x="{x+22}" y="{y+10}">{i+1}</text>' for i,(x,y) in enumerate(groups))
html = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>照片双辐 · 造型对照</title>
<style>body{margin:32px;background:#111820;color:#e9edf2;font:16px/1.7 system-ui}h1{font-size:26px}.views{display:grid;grid-template-columns:1fr 1fr;gap:24px}figure{margin:0}svg,img{width:100%;background:white}figcaption{padding:12px 0}text{font:32px system-ui;fill:#ffd16b;stroke:none}circle,ellipse{fill:none;stroke:#ffd16b;stroke-width:4}@media(max-width:800px){.views{grid-template-columns:1fr}}</style>
<h1>照片参考 → 8 组双辐 CAD</h1><p>人工观察标注 / 正面 CAD 投影。参考图有透视，未进行相机标定；图上标注不是工程尺寸。</p><div class="views"><figure>
<svg viewBox="0 0 1824 1368"><image href="reference.jpg" width="1824" height="1368"/><ellipse cx="923" cy="668" rx="653" ry="646"/>MARKERS</svg>
<figcaption>原始照片与 8 组辐条端部位置。椭圆为人工近似外轮廓。</figcaption></figure><figure><img src="cad-front.svg" alt="CAD 正面投影"><figcaption>22×8.5J ET35 假设样例：8 组 / 16 根支臂，U 形分叉进入实体。中心盖与周圈装饰件不进入此工程投影。</figcaption></figure></div><p>仍需校准：轮唇宽度、辐根细节、凹面、背部厚度和真实规格。图片、标注和投影分别保留，原照片不作修改。</p></html>'''
(args.output / 'index.html').write_text(html.replace('MARKERS', markers))
print(args.output / 'index.html')
