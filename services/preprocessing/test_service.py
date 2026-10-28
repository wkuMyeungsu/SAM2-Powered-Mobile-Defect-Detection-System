"""HTTP 동작 확인: /preprocess, /frame → 가짜 inference 로 전송."""
import base64
import json
import os
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeInference(BaseHTTPRequestHandler):
    received = []
    status = 200
    delay = 0.0

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        time.sleep(FakeInference.delay)
        FakeInference.received.append(body)
        out = json.dumps({"frame_id": body.get("frame_id"), "objects": []}).encode()
        self.send_response(FakeInference.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


INF_PORT, PRE_PORT = free_port(), free_port()
os.environ["INFERENCE_URL"] = f"http://127.0.0.1:{INF_PORT}/infer"
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", INF_PORT), FakeInference).serve_forever, daemon=True).start()

import main  # noqa: E402  (환경 변수를 먼저 정해야 함)
from test_preprocess import synthetic_frame  # noqa: E402

threading.Thread(target=main.serve, args=(PRE_PORT,), daemon=True).start()
BASE = f"http://127.0.0.1:{PRE_PORT}"


def post(path, body, headers):
    req = urllib.request.Request(BASE + path, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def wait_until(cond, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class ServiceTest(unittest.TestCase):
    def setUp(self):
        FakeInference.received.clear()
        FakeInference.status, FakeInference.delay = 200, 0.0
        wait_until(lambda: not main.forwarder.snapshot()["pending"])

    def test_preprocess_raw_bytes(self):
        status, out = post("/preprocess", synthetic_frame(),
                           {"Content-Type": "image/jpeg", "X-Frame-Id": "7", "X-Timestamp": "1761600000.25"})
        self.assertEqual(status, 200)
        self.assertEqual((out["frame_id"], out["timestamp"]), ("7", 1761600000.25))
        self.assertEqual(len(out["candidates"]), 2)
        self.assertIn("original_image", out)
        time.sleep(0.2)
        self.assertEqual(FakeInference.received, [])  # /preprocess 는 inference 로 보내지 않는다

    def test_frame_forwards_json(self):
        body = json.dumps({"frame_id": 42, "timestamp": 1.5,
                           "image": "data:image/jpeg;base64," + base64.b64encode(synthetic_frame()).decode()})
        status, out = post("/frame", body.encode(), {"Content-Type": "application/json"})
        self.assertEqual(status, 202)
        self.assertEqual((out["frame_id"], out["status"], out["candidates"]), (42, "queued", 2))
        self.assertTrue(wait_until(lambda: FakeInference.received))
        sent = FakeInference.received[0]
        self.assertEqual((sent["frame_id"], sent["timestamp"]), (42, 1.5))
        self.assertEqual(len(sent["candidates"]), 2)
        for key in ("image", "original_image", "width", "height", "scale"):
            self.assertIn(key, sent)

    def test_latest_frame_wins(self):
        FakeInference.delay = 0.5  # inference 가 느린 동안 프레임 4장이 들어온다
        frame = synthetic_frame()
        statuses = [post("/frame", frame, {"Content-Type": "image/jpeg", "X-Frame-Id": str(i)})[1]["status"]
                    for i in range(4)]
        self.assertEqual(statuses[0], "queued")
        self.assertIn("replaced", statuses[1:])
        self.assertTrue(wait_until(lambda: not main.forwarder.snapshot()["pending"]
                                   and len(FakeInference.received) >= 2))
        time.sleep(0.6)
        ids = [r["frame_id"] for r in FakeInference.received]
        self.assertEqual(ids[0], "0")
        self.assertEqual(ids[-1], "3")  # 마지막 프레임은 반드시 간다
        self.assertLess(len(ids), 4)    # 밀린 중간 프레임은 버렸다

    def test_inference_busy_counted(self):
        before = main.forwarder.snapshot()["busy"]
        FakeInference.status = 503
        post("/frame", synthetic_frame(), {"Content-Type": "image/jpeg"})
        self.assertTrue(wait_until(lambda: main.forwarder.snapshot()["busy"] == before + 1))

    def test_errors(self):
        self.assertEqual(post("/frame", b"x", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(post("/frame", b"not an image", {"Content-Type": "image/jpeg"})[0], 400)
        self.assertEqual(post("/frame", b'{"image": 3}', {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(post("/frame", b'[1]', {"Content-Type": "application/json"})[0], 400)
        self.assertEqual(post("/frame", synthetic_frame(),
                              {"Content-Type": "image/jpeg", "X-Timestamp": "abc"})[0], 400)
        self.assertEqual(post("/nope", b"x", {"Content-Type": "image/jpeg"})[0], 404)

    def test_health(self):
        with urllib.request.urlopen(BASE + "/health", timeout=5) as r:
            out = json.loads(r.read())
        self.assertEqual(out["status"], "ok")
        self.assertIn("sent", out["forward"])
        self.assertEqual(out["settings"]["white_balance"], "on")

    def test_latest_jpg(self):
        post("/preprocess", synthetic_frame(), {"Content-Type": "image/jpeg"})
        with urllib.request.urlopen(BASE + "/latest.jpg", timeout=5) as r:
            self.assertEqual(r.headers["Content-Type"], "image/jpeg")
            self.assertEqual(r.read()[:2], b"\xff\xd8")


if __name__ == "__main__":
    unittest.main()
