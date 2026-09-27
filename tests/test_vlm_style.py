import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest


def fake_server(answers):
    """OpenAI-style chat endpoint returning `answers` in turn; records the request bodies."""
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            text = answers[min(len(seen), len(answers)) - 1]
            body = json.dumps({"choices": [{"message": {"content": text}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/v1", seen


def test_detect_merges_photos_and_turns_thinking_off():
    from wheelcam.vlm_style import detect
    server, url, seen = fake_server([
        'Sure: {"center_pad": false, "arm_groove": true, "hub_valleys": false}',
        '{"center_pad": true, "arm_groove": false, "hub_valleys": false}'])
    try:
        photo = np.full((64, 64, 3), .5)
        style = detect([photo, photo], url=url, model="m")
    finally:
        server.shutdown()
    assert style == {"center_pad": True, "arm_groove": True, "hub_valleys": False, "source": "vlm", "model": "m"}
    assert all(b["chat_template_kwargs"] == {"enable_thinking": False} for b in seen)


def test_detect_rejects_an_answer_without_the_features():
    from wheelcam.vlm_style import detect
    server, url, _ = fake_server(['{"groups": 6}'])
    try:
        with pytest.raises(ValueError, match="lacks"):
            detect([np.zeros((8, 8, 3))], url=url, model="m")
    finally:
        server.shutdown()
