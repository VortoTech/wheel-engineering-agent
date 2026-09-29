import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('endpoint_check', Path(__file__).resolve().parents[1] / 'skills/localpilot/scripts/check_endpoint.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_handoff_requires_named_model_and_preserves_scope(monkeypatch):
    calls = []
    def request(url, key, payload=None):
        calls.append(payload)
        return {'data': [{'id': 'wheel-model'}]} if payload is None else {'choices': [{'message': {'content': 'OK'}}]}
    monkeypatch.setattr(module, 'request_json', request)
    result = module.check('http://localhost:8000/v1/', 'wheel-model', True)
    assert result['text_probe'] == 'passed'
    assert result['vision_validation'] == result['wheel_acceptance'] == 'not_run'
    assert result['environment']['WHEELCAM_CHAT_MODEL'] == 'wheel-model'
    assert len(calls) == 2


@pytest.mark.parametrize('entry', [{'id': 'x', 'simulated': True}, {'id': 'x', 'owned_by': 'localpilot'}])
def test_simulated_or_incompatible_gateway_rejected(monkeypatch, entry):
    monkeypatch.setattr(module, 'request_json', lambda *args: {'data': [entry]})
    with pytest.raises(ValueError):
        module.check('http://localhost:8000/v1', 'x')


def test_missing_model_does_not_select_another(monkeypatch):
    monkeypatch.setattr(module, 'request_json', lambda *args: {'data': [{'id': 'other'}]})
    with pytest.raises(ValueError, match='not found'):
        module.check('http://localhost:8000/v1', 'wanted')


def test_credentials_in_url_are_rejected_before_network(monkeypatch):
    monkeypatch.setattr(module, 'request_json', lambda *args: pytest.fail('must not send request'))
    with pytest.raises(ValueError):
        module.check('http://user:secret@localhost/v1', 'x')
