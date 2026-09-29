"""Encode captured browser frames using their original timestamps, then add voice."""
import argparse
import json
import subprocess
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('capture_dir', type=Path)
p.add_argument('--voice', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
d = a.capture_dir.resolve()
frames = json.loads((d / 'capture.json').read_text())['frames']
if len(frames) < 2:
    raise SystemExit('Capture requires at least two frames')
lines = ['ffconcat version 1.0']
for frame, following in zip(frames, frames[1:]):
    lines += [f"file '{frame['file']}'", f"duration {max(.001, following['timestamp']-frame['timestamp']):.6f}"]
lines += [f"file '{frames[-1]['file']}'", 'duration 0.1', f"file '{frames[-1]['file']}'"]
(d / 'frames.ffconcat').write_text('\n'.join(lines) + '\n')
base = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y']
raw = d / 'workbench-raw.mp4'
subprocess.run(base + ['-safe', '0', '-i', str(d / 'frames.ffconcat'), '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=0x0c1117,setsar=1', '-r', '30', '-c:v', 'libx264', '-preset', 'fast', '-crf', '19', '-pix_fmt', 'yuv420p', str(raw)], check=True)
probe = lambda f: float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(f)],text=True))
if probe(a.voice) + 1 > probe(raw):
    raise SystemExit('Narration exceeds capture: shorten narration, do not trim speech silently')
subprocess.run(base + ['-i', str(raw), '-i', str(a.voice), '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11,adelay=1000|1000,apad', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', str(a.out)], check=True)
