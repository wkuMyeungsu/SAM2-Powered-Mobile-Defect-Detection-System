import base64
import binascii
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import preprocess

SERVICE = os.environ.get("SERVICE_NAME", "unknown")
PORT = int(os.environ.get("PORT", "8000"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", str(10 * 1024 * 1024)))
CONFIG = preprocess.Config()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            self.send_json(200, {"service": SERVICE, "status": "ok"})
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/preprocess":
            self.send_json(404, {"error": "not found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self.send_json(400, {"error": "본문이 비어 있음"})
            return
        if length > MAX_BODY:
            self.send_json(413, {"error": f"본문이 {MAX_BODY}바이트를 넘음"})
            return
        body = self.rfile.read(length)

        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        frame_id = self.headers.get("X-Frame-Id") or parse_qs(url.query).get("frame_id", [None])[0]
        try:
            if ctype == "application/json":
                req = json.loads(body)
                frame_id = req.get("frame_id", frame_id)
                data = base64.b64decode(req["image"], validate=True)
            elif ctype in ("image/jpeg", "image/png", "application/octet-stream"):
                data = body
            else:
                self.send_json(415, {"error": f"지원하지 않는 Content-Type: {ctype}"})
                return
            result = preprocess.run(data, frame_id, CONFIG)
        except (KeyError, json.JSONDecodeError, binascii.Error, AttributeError):
            self.send_json(400, {"error": 'JSON 본문은 {"frame_id": ..., "image": "<base64>"} 형식이어야 함'})
            return
        except preprocess.PreprocessError as e:
            self.send_json(400, {"error": str(e)})
            return
        self.send_json(200, result)

    def log_message(self, fmt, *args):
        # 헬스체크가 10초마다 찍히지 않게 한다.
        if "/health" not in self.path:
            super().log_message(fmt, *args)


ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
