"""Generate Chinese narration. Neural sends only narration text to the TTS service."""
import argparse
import asyncio
import json
import math
import subprocess
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--provider', choices=['neural', 'macos'], default='neural')
p.add_argument('--voice', default=None)
p.add_argument('--rate', default=None)
a = p.parse_args()
a.voice = a.voice or ('zh-CN-XiaoxiaoNeural' if a.provider == 'neural' else 'Tingting')
a.rate = a.rate or ('-5%' if a.provider == 'neural' else '200')
root = Path(__file__).resolve().parents[1]
out = root / 'public/voice'
out.mkdir(parents=True, exist_ok=True)
scenes = json.loads((root / 'scripts/narration.json').read_text())
for scene in scenes:
    text = out / (scene['id'] + '.txt')
    text.write_text(scene['text'])
    source = out / (scene['id'] + ('.mp3' if a.provider == 'neural' else '.aiff'))
    wav = out / (scene['id'] + '.wav')
    if a.provider == 'neural':
        import edge_tts
        asyncio.run(edge_tts.Communicate(scene['text'], a.voice, rate=a.rate).save(str(source)))
    else:
        subprocess.run(['say', '-v', a.voice, '-r', a.rate, '-f', str(text), '-o', str(source)], check=True)
    subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-ar', '48000', '-ac', '1', str(wav)], check=True)
    duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(wav)], text=True))
    scene.update(audio='voice/' + scene['id'] + '.wav', audio_seconds=duration, frames=math.ceil((duration + 1.2) * 30))
    scene.pop('text')
metadata = dict(provider=a.provider, voice=a.voice, rate=a.rate, fps=30, scenes=scenes, frames=sum(s['frames'] for s in scenes))
(root / 'src/voice.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
(out / 'manifest.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
print(f"Chinese narration: {metadata['frames']/30:.2f}s including scene pauses, voice={a.voice}")
