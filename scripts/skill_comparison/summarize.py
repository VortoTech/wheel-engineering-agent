"""Produce a compact, auditable summary without changing raw evaluation records."""
import argparse
import json
from pathlib import Path


def summarize(root):
    raw=json.loads((root/'summary.json').read_text())
    results=[]
    for original in raw['results']:
        row=dict(original)
        if 'score' in row:
            row['score']={k:v for k,v in row['score'].items() if k!='circular_edges'}
        dest=root/row['case']/row['arm']
        if (dest/'response.json').exists():
            response=json.loads((dest/'response.json').read_text())
            row['model_usage']=response.get('usage')
            row['finish_reason']=response['choices'][0].get('finish_reason')
        if not row['completed'] and (dest/'worker.log').exists():
            row['failure_detail']=(dest/'worker.log').read_text().strip().splitlines()[-1]
        if not row['completed']:
            detail=row.get('failure_detail','')
            row['failure_category']=('output_truncated' if row.get('finish_reason')=='length' else
                'unsupported_operation' if 'unsupported CSG operation' in detail else
                'timeout' if 'wall-time' in row.get('error','') else 'worker_or_evaluator_failure')
        results.append(row)
    groups={}
    for arm in ('A','B','C'):
        rows=[r for r in results if r['arm']==arm]
        groups[arm]={'finished':len(rows),'expected':raw['attempts']//3,
            'completed':sum(r['completed'] for r in rows),
            'valid_single_solid':sum(r.get('score',{}).get('valid_single_solid') is True for r in rows),
            'step_roundtrip':sum(r.get('score',{}).get('step_roundtrip') is True for r in rows),
            'seconds_total':round(sum(r['seconds'] for r in rows),2),
            'missing_et_declared':sum(r.get('missing_et_declared') is True for r in rows),
            'missing_et_assessed':sum(r.get('missing_et_declared') is not None for r in rows),
            'output_truncated':sum(r.get('finish_reason')=='length' for r in rows)}
    return {'attempts':raw['attempts'],'finished':raw['finished'],'groups':groups,'results':results,
            'scope':'Development cases, bounded CSG baseline, simplified machining STEP. Truncation is not a geometry-quality comparison. Missing assessment is not a pass.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(summarize(a.root),ensure_ascii=False,indent=2))
