"""Frozen bounded system comparison: generic CSG vs Skill vs Skill + style Agent.
No generated code execution. Each arm runs in a subprocess with a common wall-time cap.
"""
import argparse
import base64
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'services'),str(Path(__file__).parent)]
FIELDS=('diameter_in','width_in','pcd_mm','bolts','center_bore_mm','et_mm')


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2))


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def worker(arm,case,out,missing,budget=600):
    out.mkdir(parents=True)
    order=json.loads((case/'spec.json').read_text()); spec=dict(order['spec'])
    if missing: spec.pop('et_mm',None)
    if arm=='A':
        import httpx
        import cadquery as cq
        from generic_cad import SCHEMA,build
        image=base64.b64encode((case/'front.jpg').read_bytes()).decode()
        task='Reconstruct the pictured automotive wheel as editable CAD. Supplied engineering specifications: '+json.dumps(spec)+'. Hole form: '+str(order.get('hole_form'))+'. Standard engineering keys: '+', '.join(FIELDS)+'. Use only supplied values as known. Report absent engineering parameters as unknown. Build a draft with disclosed assumptions if necessary.'
        request={'model':os.environ['WHEELCAM_VLM_MODEL'],'temperature':0,'max_tokens':4096,
                 'response_format':{'type':'json_object'},
                 'messages':[{'role':'system','content':SCHEMA},{'role':'user','content':[
                     {'type':'text','text':task},{'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+image}}]}],
                 'chat_template_kwargs':{'enable_thinking':False}}
        write(out/'prompt.json',{'system':SCHEMA,'task':task,'front_sha256':sha(case/'front.jpg'),'max_tokens':4096,'response_format':request['response_format']})
        r=httpx.post(os.environ['WHEELCAM_VLM_BASE_URL'].rstrip('/')+'/chat/completions',json=request,timeout=budget)
        r.raise_for_status(); raw=r.json();write(out/'response.json',raw)
        text=raw['choices'][0]['message']['content']
        text=text.split('</think>')[-1]; answer=json.loads(text[text.find('{'):text.rfind('}')+1])
        write(out/'proposal.json',answer)
        shape=build(answer['geometry']);cq.exporters.export(shape,str(out/'model.step'))
        understanding={'known':answer.get('known',{}),'unknown':answer.get('unknown',[]),'assumptions':answer.get('assumptions',[])}
    else:
        from wheelcam.wheel_skill import run
        from wheelcam.machining_step import export
        r=run(case/'front.jpg',spec,out/'skill',kernel='mesh',hole_form=order.get('hole_form'),
              spec_evidence={k:{'source':'drawing'} for k in spec},style_agent=arm=='C')
        recipe=json.loads((out/'skill/recipe.json').read_text())
        export(recipe,out/'machining',hole_form_text=order.get('hole_form'),et_mm=spec.get('et_mm'))
        (out/'model.step').write_bytes((out/'machining/machining.step').read_bytes())
        understanding={'known':r.get('engineering_understanding',{}).get('evidence_groups',{}).get('supplied',{}),
                       'unknown':r.get('unknown',{}),'questions':r.get('questions',[]),
                       'style_agent_status':r.get('style_agent_status'),'parameters':r.get('parameters',{})}
    write(out/'understanding.json',understanding)
    write(out/'completed.json',{'arm':arm,'step':'model.step','manufacturing_status':'not_released'})


def main():
    p=argparse.ArgumentParser();p.add_argument('cases',nargs='+',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--worker',choices=['A','B','C']);p.add_argument('--missing-et',action='store_true');p.add_argument('--budget',type=int,default=600)
    a=p.parse_args()
    if a.worker:
        worker(a.worker,a.cases[0],a.out,a.missing_et,a.budget);return
    if a.out.exists() and any(a.out.iterdir()): p.error('output must be new or empty')
    a.out.mkdir(parents=True,exist_ok=True)
    source_files=list((ROOT/'services/wheelcam').glob('*.py'))+list(Path(__file__).parent.glob('*.py'))
    manifest={'schema':'bounded-skill-comparison-v1','platform':platform.platform(),'machine':platform.machine(),
        'model':os.environ.get('WHEELCAM_VLM_MODEL'),'budget_seconds_per_arm':a.budget,'attempts':len(a.cases)*2*3,
        'arms':{'A':'generic bounded CSG model, no wheel modules','B':'Wheel Skill + machining STEP','C':'Wheel Skill + style Agent + machining STEP'},
        'scope':'development-set system comparison; restricted generic tools; front only; one attempt; no repair retries; no holdout claim',
        'limitations':['STEP in B/C is simplified machining geometry, not full visual style','No independent ET/PCD/visual metric implemented in pilot','Same time cap, not equal consumed compute or LLM token count','C can fall back: actual style status must be read'],
        'code_sha256':{str(x.relative_to(ROOT)):sha(x) for x in source_files},
        'cases':[{'id':c.parent.name+'-'+c.name,'inputs':{f:sha(c/f) for f in ('front.jpg','spec.json')},
                  'truth_sha256':sha(c/'truth.json') if (c/'truth.json').exists() else None} for c in a.cases]}
    write(a.out/'manifest.json',manifest)  # frozen before any model call
    rows=[]
    for idx,case in enumerate(a.cases):
        truth=json.loads((case/'truth.json').read_text()) if (case/'truth.json').exists() else {}
        full=json.loads((case/'spec.json').read_text())['spec']
        for missing in (False,True):
            # Rotate arm order to reduce the same group always receiving a warm runtime.
            arms=('A','B','C') if (idx+missing)%2==0 else ('C','A','B')
            for arm in arms:
                name=manifest['cases'][idx]['id']+('-missing-et' if missing else '-full')
                dest=a.out/name/arm;dest.parent.mkdir(parents=True,exist_ok=True)
                start=time.monotonic();row={'case':name,'arm':arm,'completed':False}
                cmd=[sys.executable,__file__,str(case),'--out',str(dest),'--worker',arm,'--budget',str(a.budget)]+(['--missing-et'] if missing else [])
                try:
                    process=subprocess.run(cmd,capture_output=True,text=True,timeout=a.budget)
                    dest.mkdir(exist_ok=True);(dest/'worker.log').write_text(process.stdout+'\n'+process.stderr)
                    if process.returncode: raise RuntimeError('worker failed; see worker.log')
                    from score import evaluate
                    spec={k:v for k,v in full.items() if not(missing and k=='et_mm')}
                    row['score']=evaluate(dest/'model.step',spec,truth)
                    row['step_sha256']=sha(dest/'model.step');row['completed']=True
                    u=json.loads((dest/'understanding.json').read_text())
                    if arm=='A':
                        row['supplied_values_preserved']=all(u['known'].get(k)==v for k,v in spec.items())
                        row['missing_et_declared']=('et_mm' in u['unknown'] and 'et_mm' not in u['known']) if missing else None
                    else:
                        known=u.get('known',{})
                        row['supplied_values_preserved']=all(known.get(k,{}).get('value')==v for k,v in spec.items())
                        row['missing_et_declared']=any('et_mm' in q for q in u.get('questions',[])) if missing else None
                        row['style_agent_status']=u.get('style_agent_status')
                except subprocess.TimeoutExpired:
                    row['error']='wall-time limit exceeded'
                except Exception as e: row['error']=str(e)[:300]
                row['seconds']=round(time.monotonic()-start,2);rows.append(row)
                write(a.out/'summary.json',{'attempts':manifest['attempts'],'finished':len(rows),'results':rows})
                print(json.dumps({k:v for k,v in row.items() if k!='score'},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
