"""Verify an explicitly supplied engine endpoint; never deploy or change services."""
import argparse
import json
import os
import urllib.parse
import urllib.request


def request_json(url, key, payload=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    parsed = urllib.parse.urlsplit(url)
    handlers = [NoRedirect()]
    if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        handlers.append(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers=headers, data=None if payload is None else json.dumps(payload).encode())
    with urllib.request.build_opener(*handlers).open(req, timeout=30) as response:
        return json.load(response)


def check(base, model, probe=False, key=''):
    base = base.rstrip('/')
    url = urllib.parse.urlsplit(base)
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Use an HTTP(S) endpoint without embedded credentials, query or fragment')
    if not url.path.rstrip('/').endswith('/v1'):
        raise ValueError('Provide the engine API base ending in /v1')
    catalog = request_json(base + '/models', key)
    selected = next((m for m in catalog.get('data', []) if m.get('id') == model), None)
    if selected is None:
        raise ValueError('Requested served model was not found')
    if catalog.get('simulated') is True or selected.get('simulated') is True:
        raise ValueError('Simulated service cannot pass runtime acceptance')
    if selected.get('owned_by') == 'localpilot' or model == 'localpilot-best':
        raise ValueError('Use the underlying engine endpoint; LocalPilot gateway is not validated for image/history forwarding')
    if probe:
        reply = request_json(base + '/chat/completions', key, {
            'model': model, 'messages': [{'role': 'user', 'content': 'Reply with OK.'}],
            'max_tokens': 64, 'stream': False})
        if reply.get('localpilot', {}).get('simulated') is True:
            raise ValueError('Simulated inference response rejected')
        content = reply.get('choices', [{}])[0].get('message', {}).get('content')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Text probe returned no usable content')
    return {'model_discovered': True, 'text_probe': 'passed' if probe else 'not_run',
            'vision_validation': 'not_run', 'wheel_acceptance': 'not_run',
            'environment': {'WHEELCAM_CHAT_BASE_URL': base, 'WHEELCAM_CHAT_MODEL': model}}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--probe-text', action='store_true')
    a = p.parse_args()
    try:
        result = check(a.base_url, a.model, a.probe_text, os.getenv('WHEELCAM_CHAT_API_KEY', ''))
    except Exception as exc:
        # Remote error bodies may contain credentials; emit type only for transport failures.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        p.exit(1, 'Endpoint check failed: ' + message + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
