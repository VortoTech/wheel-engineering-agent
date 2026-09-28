import importlib.util
from pathlib import Path
import pytest

path=Path(__file__).parents[1]/'scripts/skill_comparison/generic_cad.py'
spec=importlib.util.spec_from_file_location('generic_cad',path)
generic=importlib.util.module_from_spec(spec);spec.loader.exec_module(generic)


def test_generic_boolean_and_roundtrip(tmp_path):
    import cadquery as cq
    shape=generic.build({'op':'cut','base':{'op':'cylinder','r':20,'h':10,'xyz':[0,0,0]},
                         'tools':[{'op':'cylinder','r':5,'h':12,'xyz':[0,0,-1]}]})
    assert shape.isValid() and len(shape.Solids())==1
    step=tmp_path/'part.step';cq.exporters.export(shape,str(step))
    spec=importlib.util.spec_from_file_location('comparison_score',path.with_name('score.py'))
    scorer=importlib.util.module_from_spec(spec);spec.loader.exec_module(scorer)
    result=scorer.evaluate(step,{'center_bore_mm':10},{'lip_od_mm':40,'overall_width_mm':10})
    assert result['step_roundtrip'] and result['center_bore_circular_edge_candidate']
    assert all(v['error_mm']==0 for v in result['dimensions'].values())


@pytest.mark.parametrize('node',[{'op':'exec','code':'1+1'}, {'op':'cylinder','r':float('nan'),'h':5,'xyz':[0,0,0]},
    {'op':'radial','count':1000000,'node':{}},{'op':'box','size':[-1,2,3],'xyz':[0,0,0]}])
def test_model_cannot_execute_code_or_escape_bounds(node):
    with pytest.raises(ValueError): generic.build(node)


def test_summary_keeps_failures_and_unassessed_metrics(tmp_path):
    import json
    spec=importlib.util.spec_from_file_location('comparison_summary',path.with_name('summarize.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'summary.json').write_text(json.dumps({'attempts':6,'finished':2,'results':[
        {'case':'full','arm':'A','completed':False,'seconds':100},
        {'case':'missing-et','arm':'B','completed':True,'seconds':20,
         'missing_et_declared':True,'score':{'valid_single_solid':False,'step_roundtrip':False}}
    ]}))
    result=module.summarize(tmp_path)
    assert result['groups']['A']['expected']==2
    assert result['groups']['A']['completed']==0
    assert result['groups']['A']['missing_et_assessed']==0
    assert result['groups']['B']['completed']==1
    assert result['groups']['B']['valid_single_solid']==0
    assert result['groups']['C']['finished']==0
