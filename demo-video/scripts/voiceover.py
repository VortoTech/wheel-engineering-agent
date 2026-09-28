"""Generate replaceable local Chinese voiceover; no cloud credentials or uploads."""
import argparse
import json
import math
import subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--voice',default='Tingting');p.add_argument('--rate',type=int,default=200);a=p.parse_args()
root=Path(__file__).resolve().parents[1]; out=root/'public/voice';out.mkdir(parents=True,exist_ok=True)
scenes=json.loads((root/'scripts/narration.json').read_text())
for scene in scenes:
 text=out/(scene['id']+'.txt');text.write_text(scene['text'])
 aiff=out/(scene['id']+'.aiff');wav=out/(scene['id']+'.wav')
 subprocess.run(['say','-v',a.voice,'-r',str(a.rate),'-f',str(text),'-o',str(aiff)],check=True)
 subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(aiff),'-ar','48000','-ac','1',str(wav)],check=True)
 duration=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',str(wav)],text=True))
 scene.update(audio='voice/'+scene['id']+'.wav',audio_seconds=duration,frames=math.ceil((duration+1.2)*30))
 scene.pop('text')
metadata={'voice':a.voice,'rate':a.rate,'fps':30,'scenes':scenes,'frames':sum(s['frames'] for s in scenes)}
(root/'src/voice.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
(out/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
print(f"Chinese narration: {metadata['frames']/30:.2f}s including scene pauses, voice={a.voice}")
