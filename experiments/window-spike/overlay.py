"""Side-by-side overlays: target mask edge (cyan) over photo, and each candidate's disagreement
inside the evaluation annulus (red = model material where target is open, blue = missing material)."""
import sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion
sys.path.insert(0, str(Path(__file__).parent))
from evaluate import zone, load
S = Path.cwd()   # the work directory holding context.json, masks and outputs
photo = np.asarray(Image.open(S/'photo720.png').convert('RGB')).astype(float)
target = load(sys.argv[1]); panels = []
t = photo.copy(); edge = target & ~binary_erosion(target, iterations=1); t[edge] = [0, 255, 255]
panels.append(('target', t))
for p in sys.argv[2:]:
    c = load(p); o = photo*0.55
    o[zone & c & ~target] = [230, 40, 40]; o[zone & ~c & target] = [40, 90, 240]
    o[zone & c & target] = o[zone & c & target]*0.4 + np.array([255, 190, 60])*0.6
    panels.append((Path(p).stem, o))
w = photo.shape[1]; out = Image.new('RGB', (w*len(panels), photo.shape[0]+24), 'white'); d = ImageDraw.Draw(out)
for i, (name, arr) in enumerate(panels):
    out.paste(Image.fromarray(arr.clip(0, 255).astype('uint8')), (i*w, 24)); d.text((i*w+8, 6), name, fill='black')
out.save(S/'compare.png'); print('compare.png', out.size)
