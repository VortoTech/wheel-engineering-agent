import io
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from wheelcam.workbench import create_app
from test_workbench import make_run


def test_failed_and_timed_out_jobs_keep_logs_and_release_slot(tmp_path, monkeypatch):
    def failed(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout='stage started', stderr='geometry failure')
    monkeypatch.setattr(subprocess, 'run', failed)
    c=TestClient(create_app(tmp_path))
    assert c.post('/api/runs/text',json={'message':'wheel'}).status_code==502
    assert 'geometry failure' in next((tmp_path/'.logs').glob('*.log')).read_text()
    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 1200, output=b'partial output', stderr=b'time limit')
    monkeypatch.setattr(subprocess, 'run', timeout)
    assert c.post('/api/runs/text',json={'message':'wheel'}).status_code==504
    assert len(list((tmp_path/'.logs').glob('*.log')))==2
    assert any('partial output' in p.read_text() for p in (tmp_path/'.logs').glob('*.log'))


def test_different_versions_cannot_build_concurrently(tmp_path, monkeypatch):
    started=threading.Event(); finish=threading.Event()
    def blocked(command, **kwargs):
        started.set(); assert finish.wait(10)
        return subprocess.CompletedProcess(command,1,stdout='',stderr='test failure')
    monkeypatch.setattr(subprocess,'run',blocked)
    c=TestClient(create_app(tmp_path))
    with ThreadPoolExecutor(1) as pool:
        first=pool.submit(c.post,'/api/runs/text',json={'message':'first'})
        assert started.wait(5)
        try:
            assert c.post('/api/runs/text',json={'message':'second'}).status_code==409
        finally:
            finish.set()
        assert first.result().status_code==502
    assert c.post('/api/runs/text',json={'message':'third'}).status_code==502


def test_stale_chat_is_rejected_before_model_call(tmp_path, monkeypatch):
    make_run(tmp_path,{'flank_w':2})
    import wheelcam.recipe_chat as rc
    def unexpected(*args,**kwargs):
        raise AssertionError('model must not be called')
    monkeypatch.setattr(rc,'turn',unexpected)
    c=TestClient(create_app(tmp_path))
    assert c.post('/api/runs/m1/chat',json={'message':'change','expected_recipe_sha256':'old'}).status_code==409
    assert not (tmp_path/'m1/chat').exists()


def test_image_style_opt_in_requires_configuration_and_survives_intake(tmp_path, monkeypatch):
    c=TestClient(create_app(tmp_path))
    monkeypatch.delenv('WHEELCAM_VLM_BASE_URL',raising=False)
    buf=io.BytesIO();Image.new('RGB',(32,32)).save(buf,'PNG')
    kwargs={'files':{'front':('front.png',buf.getvalue())},'data':{'style_agent':'true'}}
    assert c.post('/api/runs/image',**kwargs).status_code==422
    monkeypatch.setenv('WHEELCAM_VLM_BASE_URL','http://local.invalid/v1')
    monkeypatch.setenv('WHEELCAM_VLM_MODEL','test-vlm')
    def success(command,**kw):
        order=json.loads((Path(command[2])/'spec.json').read_text())
        assert order['style_agent'] is True and order['visual_check'] is True
        out=Path(command[-1]);out.mkdir()
        (out/'chain.json').write_text(json.dumps({'steps':[{'ok':True}]}))
        return subprocess.CompletedProcess(command,0,stdout='finished',stderr='')
    monkeypatch.setattr(subprocess,'run',success)
    assert c.get('/api/capabilities').json()['style_agent_configured'] is True
    assert c.post('/api/runs/image',**kwargs).status_code==200
