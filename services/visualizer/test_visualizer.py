"""visualizer 단위 테스트. gateway 없이 돈다.

docker compose run --rm visualizer python -m unittest -v test_visualizer
"""
import base64
import os
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
    def __init__(self):
        self.sent = []

    async def post(self, url, json):
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

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def send(self, frame_id, objects, **extra):
        body = {"frame_id": frame_id, "image": self.image, "objects": objects, **extra}
        return self.client.post("/visualize", json=body)

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
