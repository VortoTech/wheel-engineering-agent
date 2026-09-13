"""One SAM object per clicked arm; keep each object's arm-sized mask; union = material mask."""
import json, numpy as np, torch
from PIL import Image
from transformers import Sam2Model, Sam2Processor
name = 'facebook/sam2.1-hiera-small'; photo = Image.open('photo720.png').convert('RGB')
pos = json.load(open('sam_prompts.json'))['positive'][:-5]      # arm clicks only (drop hub clicks)
import math
ctx = json.load(open('context.json')); e = ctx['ellipse']
g = np.asarray(photo).astype(float).mean(2); v9 = np.asarray(Image.open('v9_mask.png')) > 127
run = []
for d in np.arange(0, 360, 0.25):   # trunk clicks where the pairs merge
    a = math.radians(d); x, y = int(round(e['cx']+e['rx']*.38*math.cos(a))), int(round(e['cy']+e['ry']*.38*math.sin(a)))
    if v9[y, x] and g[y, x] < 90: run.append([x, y])
    elif run: pos.append(run[len(run)//2]); run = []
model = Sam2Model.from_pretrained(name).eval(); proc = Sam2Processor.from_pretrained(name)
inputs = proc(images=photo, input_points=[[[p] for p in pos]], input_labels=[[[1] for _ in pos]], return_tensors='pt')
with torch.no_grad(): out = model(**inputs, multimask_output=True)
masks = proc.post_process_masks(out.pred_masks, inputs['original_sizes'])[0] > 0   # (objects, 3, H, W)
scores = out.iou_scores[0]
union = np.zeros(masks.shape[-2:], bool); log = []
for i in range(len(pos)):
    areas = masks[i].float().mean((1, 2))
    ok = [(float(scores[i, j]), j) for j in range(3) if 0.001 < areas[j] < 0.008 and masks[i, j, pos[i][1], pos[i][0]]]
    if ok:
        s, j = max(ok); union |= masks[i, j].numpy(); log.append((i, j, round(s, 3), round(float(areas[j]), 4)))
    if i < 4:
        for j in range(3): Image.fromarray((masks[i, j].numpy()*255).astype('uint8')).save(f'arm{i}_{j}.png')
Image.fromarray((union*255).astype('uint8')).save('sam_arms.png')
print(len(pos), 'objects;', len(log), 'kept'); print(log)
