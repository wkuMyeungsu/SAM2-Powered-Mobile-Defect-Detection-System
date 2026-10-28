"""C2 Preprocessing (port 8002)

gateway 가 보낸 프레임을 SAM 2 에 맞게 다듬고 과자 후보 박스를 찾아 inference 로 넘긴다.

- POST /frame      : 전처리 후 inference 로 전송 (gateway 가 부르는 곳). 바로 202 로 응답한다.
- POST /preprocess : 전처리 결과를 그대로 돌려준다 (확인·디버그용, inference 로 보내지 않음).
"""
import base64
import binascii
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import forward
import preprocess

SERVICE = os.environ.get("SERVICE_NAME", "preprocessing")
PORT = int(os.environ.get("PORT", "8002"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", str(10 * 1024 * 1024)))
INFERENCE_URL = os.environ.get("INFERENCE_URL", "http://inference:8003/infer")  # 비우면 전송하지 않음
INFERENCE_TIMEOUT = float(os.environ.get("INFERENCE_TIMEOUT", "10"))
CONFIG = preprocess.Config()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("preprocessing")

forwarder = forward.Forwarder(INFERENCE_URL, INFERENCE_TIMEOUT)


class BadRequest(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def parse_timestamp(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise BadRequest(400, "timestamp 는 unix 초(숫자)여야 함")


def read_frame(handler, url) -> tuple[bytes, object, object]:
    """요청에서 (이미지 바이트, frame_id, timestamp) 를 꺼낸다."""
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        raise BadRequest(400, "본문이 비어 있음")
    if length > MAX_BODY:
        raise BadRequest(413, f"본문이 {MAX_BODY}바이트를 넘음")
    body = handler.rfile.read(length)

    query = parse_qs(url.query)
    frame_id = handler.headers.get("X-Frame-Id") or query.get("frame_id", [None])[0]
    timestamp = handler.headers.get("X-Timestamp") or query.get("timestamp", [None])[0]

    ctype = (handler.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            req = json.loads(body)
            image = req["image"]
            if image.startswith("data:"):
                image = image.split(",", 1)[1]
            data = base64.b64decode(image, validate=True)
        except (KeyError, TypeError, json.JSONDecodeError, binascii.Error, AttributeError, UnicodeDecodeError):
            raise BadRequest(400, 'JSON 본문은 {"frame_id": ..., "timestamp": ..., "image": "<base64>"} 형식이어야 함')
        frame_id = req.get("frame_id", frame_id)
        timestamp = req.get("timestamp", timestamp)
    elif ctype in ("image/jpeg", "image/png", "application/octet-stream"):
        data = body
    else:
        raise BadRequest(415, f"지원하지 않는 Content-Type: {ctype}")
    return data, frame_id, parse_timestamp(timestamp)


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
            self.send_json(200, {
                "service": SERVICE,
                "status": "ok",
                "inference_url": INFERENCE_URL or None,
                "forward": forwarder.snapshot(),
            })
        else:
            self.send_json(404, {"error": "not found"})

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in ("/preprocess", "/frame"):
            self.send_json(404, {"error": "not found"})
            return
        try:
            data, frame_id, timestamp = read_frame(self, url)
            result = preprocess.run(data, frame_id, CONFIG, timestamp)
        except BadRequest as e:
            self.send_json(e.status, {"error": str(e)})
            return
        except preprocess.PreprocessError as e:
            self.send_json(400, {"error": str(e)})
            return

        if url.path == "/preprocess":
            self.send_json(200, result)
            return

        status = forwarder.submit(result)
        self.send_json(202, {
            "frame_id": frame_id,
            "status": status,
            "candidates": len(result["candidates"]),
            "elapsed_ms": result["elapsed_ms"],
        })

    def log_message(self, fmt, *args):
        # 헬스체크와 프레임마다 찍히는 접근 로그는 생략한다. 오류 응답만 남긴다.
        if "/health" in self.path:
            return
        if self.path.startswith("/frame") and len(args) > 1 and str(args[1]).startswith("2"):
            return
        super().log_message(fmt, *args)


def serve(port: int = PORT):
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("preprocessing :%d → %s", port, INFERENCE_URL or "(전송 안 함)")
    server.serve_forever()


if __name__ == "__main__":
    serve()
