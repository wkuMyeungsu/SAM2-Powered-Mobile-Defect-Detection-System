"""visualizer 단위 테스트. gateway 없이 돈다.

docker compose run --rm visualizer python -m unittest -v test_visualizer
"""
import asyncio
import base64
import os
import time
import unittest

os.environ.setdefault("GATEWAY_URL", "http://127.0.0.1:9/result")

import cv2
import httpx
import numpy as np
from fastapi.testclient import TestClient

import main

W, H = 400, 200


def jpeg_b64() -> str:
    img = np.full((H, W, 3), 40, np.uint8)
    return base64.b64encode(cv2.imencode(".jpg", img)[1]).decode()


def square(cx: float, cy: float, half: float = 0.05) -> list[list[float]]:
    """0~1 좌표의 정사각형 윤곽선"""
    return [[cx - half, cy - half], [cx + half, cy - half], [cx + half, cy + half], [cx - half, cy + half]]


class FakeGateway:
    def __init__(self, delay: float = 0):
        self.sent = []
        self.delay = delay

    async def post(self, url, json):
        await asyncio.sleep(self.delay)
        self.sent.append(json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    async def aclose(self):
        pass


class VisualizerTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.gateway = FakeGateway()
        main.app.state.client = self.gateway
        self.client.post("/reset")
        self.image = jpeg_b64()
        self.accepted = 0

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def send(self, frame_id, objects, wait=True, **extra):
        """보내고, gateway 전송은 뒤에서 일어나므로 가짜 gateway 에 도착할 때까지 기다린다."""
        body = {"frame_id": frame_id, "image": self.image, "objects": objects, **extra}
        r = self.client.post("/visualize", json=body)
        if r.status_code == 200 and r.json()["status"] == "ok":
            self.accepted += 1
            deadline = time.time() + 3
            while wait and len(self.gateway.sent) < self.accepted and time.time() < deadline:
                time.sleep(0.01)
        return r

    def test_preprocessing_style_input(self):
        """frame_id 문자열, timestamp 없음, 픽셀 좌표 + width/height (preprocessing 결과를 그대로 따른 경우)"""
        contour = [[x * W, y * H] for x, y in square(0.5, 0.5)]
        r = self.send("7", [{"contour": contour, "circularity": 0.9, "result": "PASS"}], width=W, height=H)
        self.assertEqual(r.json()["status"], "ok")
        sent = self.gateway.sent[-1]
        self.assertEqual(sent["frame_id"], "7")
        self.assertIsNone(sent["timestamp"])
        self.assertIsNone(sent["latency_ms"])
        self.assertEqual(sent["objects"][0]["result"], "PASS")  # 픽셀 좌표가 0~1 로 바뀌어 테두리 판정이 정상

    def test_pixel_coords_without_size_use_image_size(self):
        contour = [[W - 30, 80], [W - 1, 80], [W - 1, 120], [W - 30, 120]]  # 오른쪽 테두리에 닿음
        self.send(1, [{"contour": contour, "circularity": 0.5, "result": "FAIL"}])
        self.assertEqual(self.gateway.sent[-1]["objects"][0]["result"], "WAIT")

    def test_normalized_contour_slightly_off_screen_is_not_pixel(self):
        """화면 오른쪽 밖으로 살짝 나간 과자 (0~1 좌표인데 x 가 1 을 넘음) 는 픽셀로 착각하지 않고 WAIT"""
        self.send(1, [{"contour": square(0.98, 0.5), "circularity": 0.5, "result": "FAIL"}])
        self.assertEqual(self.gateway.sent[-1]["objects"][0]["result"], "WAIT")
        self.assertEqual(self.gateway.sent[-1]["summary"]["in_frame"], 1)

    def test_inference_style_message(self):
        """inference 가 실제로 보내는 형식: frame_id 없음(null) 가능, reason 등 추가 필드 포함"""
        obj = {"contour": square(0.5, 0.5), "circularity": 0.83, "result": "FAIL",
               "reason": "straight cut or broken", "solidity": 0.98, "fill": 0.83, "candidate": 0}
        r = self.send(None, [obj])
        self.assertEqual(r.json()["status"], "ok")
        sent = self.gateway.sent[-1]
        self.assertIsNone(sent["frame_id"])
        self.assertEqual(sent["objects"][0]["reason"], "straight cut or broken")

    def test_old_frame_dropped(self):
        self.send("10", [])
        r = self.send("9", [])
        self.assertEqual(r.json()["status"], "dropped")
        self.assertEqual(len(self.gateway.sent), 1)

    def test_non_numeric_frame_id_processed_in_arrival_order(self):
        self.assertEqual(self.send("cam1-a", []).json()["status"], "ok")
        self.assertEqual(self.send("cam1-b", []).json()["status"], "ok")

    def test_crossing_counted_once(self):
        for i, x in enumerate([0.40, 0.36, 0.32, 0.28]):  # 검사선 0.34 를 한 번 넘는다
            self.send(i, [{"contour": square(x, 0.5), "circularity": 0.6, "result": "FAIL"}], timestamp=0)
        s = self.gateway.sent[-1]["summary"]
        self.assertEqual((s["passed_total"], s["defect_total"]), (1, 1))

    def test_jitter_around_scan_line_counted_once(self):
        """SAM 2 마스크가 흔들려 과자 중심이 검사선을 넘나들어도 한 번만 센다"""
        for i, x in enumerate([0.37, 0.33, 0.355, 0.32, 0.345, 0.30]):  # 뒤로 최대 0.025 흔들림
            self.send(i, [{"contour": square(x, 0.5), "circularity": 0.9, "result": "PASS"}])
        s = self.gateway.sent[-1]["summary"]
        self.assertEqual((s["passed_total"], s["defect_total"]), (1, 0))

    def test_same_cookie_keeps_id(self):
        """같은 과자는 프레임이 바뀌어도 같은 번호, 새로 들어온 과자는 새 번호"""
        self.send(0, [{"contour": square(0.60, 0.5), "circularity": 0.9, "result": "PASS"}])
        first = self.gateway.sent[-1]["objects"][0]["id"]
        self.send(1, [{"contour": square(0.56, 0.5), "circularity": 0.9, "result": "PASS"},
                      {"contour": square(0.85, 0.5), "circularity": 0.6, "result": "FAIL"}])
        ids = [o["id"] for o in self.gateway.sent[-1]["objects"]]
        self.assertEqual(ids[0], first)
        self.assertNotEqual(ids[1], first)

    def test_cors_for_dashboard(self):
        r = self.client.options("/reset", headers={"Origin": "http://localhost:8000", "Access-Control-Request-Method": "POST"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(r.headers.get("access-control-allow-origin"), ("*", "http://localhost:8000"))

    def test_slow_gateway_does_not_block_inference(self):
        main.app.state.client = self.gateway = FakeGateway(delay=1.0)
        t0 = time.time()
        r = self.send(1, [], wait=False)
        self.assertEqual(r.json()["status"], "ok")
        self.assertLess(time.time() - t0, 0.5)

    def test_metrics(self):
        for i in range(3):
            self.send(i, [], timestamp=time.time())
        m = self.client.get("/metrics").json()
        self.assertEqual(m["counts"]["processed"], 3)
        self.assertEqual(m["counts"]["sent"], 3)
        self.assertGreater(m["fps"], 0)
        self.assertIsNotNone(m["latency_ms"]["avg"])

    def test_body_too_large(self):
        r = self.client.post("/visualize", content=b"x" * (main.MAX_BODY + 1), headers={"Content-Type": "application/json"})
        self.assertEqual(r.status_code, 413)
        self.assertIn("error", r.json())

    def test_bad_image_returns_error_field(self):
        r = self.client.post("/visualize", json={"frame_id": 1, "image": "abc", "objects": []})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())

    def test_missing_field_returns_error_field(self):
        r = self.client.post("/visualize", json={"frame_id": 1})
        self.assertEqual(r.status_code, 400)
        self.assertIn("image", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
