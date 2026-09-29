"""Private, normalized styling catalog. Never carries order metadata or engineering specs."""
import argparse
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / 'runs/style-library/catalog.json'
STYLE_FIELDS = {'flank_w', 'flank_depth', 'spoke_pad_depth', 'spoke_pad_w', 'hub_valley_depth',
                'hub_arm_w', 'concavity_exp', 'hub_z', 'lip_pockets', 'lip_pocket_depth', 'lip_rib_w',
                'spoke_pad_style', 'spoke_pad_share', 'spoke_pad_draft_deg', 'hub_crease_z', 'ring_z',
                'hub_recess_depth', 'hub_valley_draft_deg'}
RADIAL_FIELDS = {'window_r_in', 'window_r_out', 'lip_pocket_r', 'spoke_pad_r', 'hub_crease_r'}


def catalog_path():
    return Path(os.getenv('WHEELCAM_STYLE_LIBRARY', str(DEFAULT_PATH)))


def generate(source, output):
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / 'runs').resolve()):
        raise ValueError('私有造型库只能写入项目 runs/ 目录')
    entries = []
    for path in sorted(Path(source).glob('*/build/recipe.json')):
        d = json.loads(path.read_text()); d = d.get('forged', d)
        if d.get('family') != 'outline' or not d.get('outlines'):
            continue
        radius = float(d['lip_r'])
        outlines = [[[r / radius, a] for r, a in loop] for loop in d['outlines']]
        style = {k: d[k] for k in STYLE_FIELDS if k in d}
        radial = {k: [v / radius for v in d[k]] if isinstance(d[k], (list, tuple)) else d[k] / radius
                  for k in RADIAL_FIELDS if k in d}
        family = 'single' if len(outlines) == 1 else 'y_split' if len(outlines) == 2 else 'skeleton'
        label = {'single': '直辐', 'y_split': '分叉造型', 'skeleton': '多窗口造型'}[family]
        entries.append({'id': f'style-{len(entries)+1:03d}', 'description': f'{d["spokes"]} 辐{label}',
                        'family': family, 'spokes': d['spokes'], 'outlines': outlines,
                        'style': style, 'radial': radial})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({'version': 1, 'private': True, 'entries': entries}, ensure_ascii=False, indent=2))
    return len(entries)


def load_catalog():
    path = catalog_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    entries = data.get('entries', [])
    for e in entries:
        if not isinstance(e.get('id'), str) or not e['id'].startswith('style-'):
            raise ValueError('无效造型库编号')
        if e.get('family') not in {'single', 'y_split', 'skeleton'} or not 3 <= e.get('spokes', 0) <= 12:
            raise ValueError('无效造型库拓扑')
        for loop in e['outlines']:
            if len(loop) < 3 or any(not math.isfinite(r) or not math.isfinite(a) or not 0 < r < 2 for r, a in loop):
                raise ValueError('无效造型库轮廓')
    return entries


def style_recipe(entry, radius):
    # Explicit whitelist again at consumption; injected metadata/spec fields cannot enter a recipe.
    result = {k: v for k, v in entry.get('style', {}).items() if k in STYLE_FIELDS}
    result.update({k: [v * radius for v in value] if isinstance(value, list) else value * radius
                   for k, value in entry.get('radial', {}).items() if k in RADIAL_FIELDS})
    result.update(family='outline', outlines=[[[r * radius, a] for r, a in loop] for loop in entry['outlines']])
    return result


def main():
    p = argparse.ArgumentParser(description='生成仅限本机使用的私有文字造型库')
    p.add_argument('--source', type=Path, default=ROOT / 'runs/real-orders-eval/mesh')
    p.add_argument('--out', type=Path, default=DEFAULT_PATH)
    a = p.parse_args()
    print(f'Generated {generate(a.source, a.out)} private styles under runs/')


if __name__ == '__main__':
    main()
