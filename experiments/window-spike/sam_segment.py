"""SAM 2.1 mask of the dark wheel centre (spokes + hub + ring) from click-style prompts.
Prompts are placed where v9 predicts material AND the photo is dark (like a user clicking arms),
negatives on the brushed lip and on bright window pixels. Output: sam_mask.png, sam_prompts.json."""
import json, math, sys
from pathlib import Path
import numpy as np, torch
from PIL import Image
from transformers import Sam2Model, Sam2Processor
S = Path.cwd()   # the work directory holding context.json, masks and outputs
ctx = json.load(open(S/'context.json')); e = ctx['ellipse']
photo = Image.open(S/'photo720.png').convert('RGB')
g = np.asarray(photo).astype(float).mean(2)
v9 = np.asarray(Image.open(S/'v9_mask.png')) > 127
def px(rho, deg):
    a = math.radians(deg); return int(round(e['cx']+e['rx']*rho*math.cos(a))), int(round(e['cy']+e['ry']*rho*math.sin(a)))
pos, neg = [], []
for rho in (0.5, 0.66):
    # One click per dark, v9-supported arm crossing along this circle.
    run = []
    for d in np.arange(0, 360, 0.25):
        x, y = px(rho, d); ok = v9[y, x] and g[y, x] < 90
        if ok: run.append((x, y))
        elif run: pos.append(run[len(run)//2]); run = []
    if run: pos.append(run[len(run)//2])
for d in range(0, 360, 72): pos.append(px(0.22, d + 20))   # hub face, between lug holes
for d in range(0, 360, 30): neg.append(px(0.93, d))         # brushed lip
for d in np.arange(0, 360, 45):                             # window centres, when bright
    for rho in (0.45, 0.6):
        x, y = px(rho, 39.5 + 22.5 + d)
        if not v9[y, x] and g[y, x] > 140: neg.append((x, y))
device = 'mps' if torch.backends.mps.is_available() else 'cpu'
name = sys.argv[1] if len(sys.argv) > 1 else 'facebook/sam2.1-hiera-large'
model = Sam2Model.from_pretrained(name).to(device).eval(); proc = Sam2Processor.from_pretrained(name)
points = [list(p) for p in pos + neg]; labels = [1]*len(pos) + [0]*len(neg)
inputs = proc(images=photo, input_points=[[points]], input_labels=[[labels]], return_tensors='pt').to(device)
with torch.no_grad():
    out = model(**inputs, multimask_output=True)
masks = proc.post_process_masks(out.pred_masks.cpu(), inputs['original_sizes'])[0][0]  # (3,H,W)
scores = out.iou_scores.cpu()[0, 0].tolist()
best = int(np.argmax(scores))
for i, m in enumerate(masks):
    Image.fromarray((m.numpy() > 0).astype('uint8')*255).save(S/f'sam_mask_{i}.png')
Image.fromarray((masks[best].numpy() > 0).astype('uint8')*255).save(S/'sam_mask.png')
json.dump({'model': name, 'device': device, 'positive': pos, 'negative': neg, 'scores': scores, 'chosen': best},
          open(S/'sam_prompts.json', 'w'))
print(name, device, len(pos), len(neg), scores, best)
