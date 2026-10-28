"""SAM 2 없이 도는 테스트. 마스크는 시뮬레이터 형상(sim_shapes)과 가짜 분할기로 만든다.

실행: python -m unittest -v test_inference
"""
import base64
import os
import random
import unittest

import cv2
import numpy as np

os.environ["VISUALIZER_URL"] = ""  # 테스트에서는 visualizer 로 보내지 않는다

import judge  # noqa: E402
import main  # noqa: E402
from sim_shapes import TYPES, render_mask  # noqa: E402


class ThresholdSegmenter:
    """박스 안에서 밝은 픽셀을 과자로 보는 가짜 SAM 2."""

    def segment(self, rgb, boxes):
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        masks = np.zeros((len(boxes), *gray.shape), bool)
        for i, (x1, y1, x2, y2) in enumerate(boxes):
            sl = np.s_[int(y1):int(y2) + 1, int(x1):int(x2) + 1]
            masks[i][sl] = gray[sl] > 100
        return masks, np.full(len(boxes), 0.99, np.float32)


def frame_with(cookies: list[tuple[str, int, int]], size=(450, 900)) -> tuple[str, list[dict]]:
    """(형상, 중심 x, 중심 y) 목록으로 어두운 벨트 위 과자 프레임과 박스를 만든다."""
    img = np.full((*size, 3), 40, np.uint8)
    cands = []
    rng = random.Random(0)
    for shape, cx, cy in cookies:
        m = render_mask(shape, 78, 55, rng)
        h, w = m.shape
        x0, y0 = cx - w // 2, cy - h // 2
        region = img[max(0, y0):y0 + h, max(0, x0):x0 + w]
        sub = m[max(0, -y0):max(0, -y0) + region.shape[0], max(0, -x0):max(0, -x0) + region.shape[1]] > 0
        region[sub] = (75, 138, 201)
        ys, xs = np.nonzero(img[..., 2] == 201)
        cands.append({"bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())]})
        img[..., 2][img[..., 2] == 201] = 200  # 다음 과자 박스 계산과 섞이지 않게 표시를 바꾼다
    ok, buf = cv2.imencode(".png", img)
    return base64.b64encode(buf).decode(), cands


class JudgeTest(unittest.TestCase):
    def test_sim_shapes_are_classified(self):
        rng = random.Random(42)
        th = judge.Thresholds()
        for shape in ["round", *TYPES]:
            for _ in range(100):
                m = judge.measure(judge.clean_mask(render_mask(shape, 78, 55, rng)))
                result, _ = judge.decide(m, th)
                self.assertEqual(result, "PASS" if shape == "round" else "FAIL", f"{shape}: {m}")

    def test_tilted_camera_normal_cookies_pass(self):
        """카메라가 기울어 원이 15% 납작해지고 흐려져도 정상품은 PASS."""
        rng = random.Random(11)
        th = judge.Thresholds()
        for diameter in (50, 78, 120):
            for _ in range(50):
                m = render_mask("round", diameter, 55, rng).astype(np.uint8) * 255
                m = cv2.GaussianBlur(cv2.resize(m, None, fx=1, fy=0.85, interpolation=cv2.INTER_AREA), (0, 0), 1.5)
                result, reason = judge.decide(judge.measure(judge.clean_mask(m > 127)), th)
                self.assertEqual(result, "PASS", f"d={diameter}: {reason}")

    def test_reason_matches_defect_type(self):
        rng = random.Random(7)
        th = judge.Thresholds()
        want = {"bite": "notch", "wedge": "notch", "cut": "straight cut", "half": "straight cut"}
        for strength in (20, 55, 100):
            for shape, prefix in want.items():
                for _ in range(50):
                    m = judge.measure(judge.clean_mask(render_mask(shape, 78, strength, rng)))
                    result, reason = judge.decide(m, th)
                    if result == "FAIL":
                        self.assertTrue(reason.startswith(prefix), f"{shape} s={strength}: {reason} {m}")

    def test_clean_mask_fills_holes_and_drops_specks(self):
        m = np.zeros((100, 100), np.uint8)
        cv2.circle(m, (50, 50), 30, 1, -1)
        cv2.circle(m, (45, 45), 4, 0, -1)  # 초코칩 구멍
        m[2:5, 2:5] = 1                     # 먼지
        c = judge.clean_mask(m)
        self.assertEqual(c[45, 45], 1)
        self.assertEqual(c[3, 3], 0)

    def test_empty_mask(self):
        self.assertIsNone(judge.measure(judge.clean_mask(np.zeros((50, 50), np.uint8))))


class InferApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fastapi.testclient import TestClient

        main.segmenter = ThresholdSegmenter()
        cls.client = TestClient(main.app).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def test_health(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")

    def test_non_cookie_candidate_is_dropped(self):
        image, cands = frame_with([("round", 200, 100)])
        img = cv2.imdecode(np.frombuffer(base64.b64decode(image), np.uint8), cv2.IMREAD_COLOR)
        cv2.rectangle(img, (500, 200), (560, 260), (150, 150, 150), cv2.FILLED)  # 회색 화면 글자 상자
        _, buf = cv2.imencode(".png", img)
        cands.append({"bbox": [500, 200, 560, 260]})
        r = self.client.post("/infer", json={"frame_id": 3, "image": base64.b64encode(buf).decode(), "candidates": cands})
        self.assertEqual([o["candidate"] for o in r.json()["objects"]], [0])
        self.assertEqual([(d["candidate"], d["why"]) for d in r.json()["dropped"]], [(1, "no cookie color in box")])

    def test_merged_and_duplicate_boxes(self):
        """쿠키 둘을 묶은 큰 박스는 둘로 나누고, 한 쿠키에 겹친 박스 둘은 하나로 합친다."""
        image, cands = frame_with([("round", 200, 150), ("round", 350, 150), ("round", 600, 300)])
        merged = {"bbox": [100, 50, 450, 250]}
        dup = [{"bbox": [550, 250, 610, 350]}, {"bbox": [590, 250, 660, 350]}]
        r = self.client.post("/infer", json={"frame_id": 4, "image": image, "candidates": [merged, *dup]})
        objs = r.json()["objects"]
        self.assertEqual(len(objs), 3, objs)
        self.assertEqual(sorted(o["candidate"] for o in objs), [0, 0, 1])
        self.assertTrue(all(o["result"] == "PASS" for o in objs))

    def test_pass_fail_and_edge(self):
        image, cands = frame_with([("round", 200, 100), ("wedge", 450, 250), ("round", 20, 380)])
        r = self.client.post("/infer", json={"frame_id": "7", "timestamp": 1.5, "image": image, "candidates": cands})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["frame_id"], "7")
        self.assertEqual([o["result"] for o in body["objects"]], ["PASS", "FAIL", "WAIT"])
        self.assertEqual([o["candidate"] for o in body["objects"]], [0, 1, 2])
        for o in body["objects"]:
            pts = np.array(o["contour"])
            self.assertGreaterEqual(len(pts), 3)
            self.assertTrue(((pts >= 0) & (pts <= 1)).all(), "contour 는 0~1 정규화 좌표")

    def test_no_candidates(self):
        image, _ = frame_with([])
        r = self.client.post("/infer", json={"frame_id": 1, "image": image, "candidates": []})
        self.assertEqual(r.json()["objects"], [])

    def test_bad_requests(self):
        self.assertEqual(self.client.post("/infer", json={"frame_id": 1, "image": "!!"}).status_code, 400)
        self.assertEqual(self.client.post("/infer", content=b"not json").status_code, 400)
        image, _ = frame_with([])
        r = self.client.post("/infer", json={"image": image, "candidates": [{"bbox": [1, 2]}]})
        self.assertEqual(r.status_code, 400)

    def test_busy(self):
        class Locked:
            def locked(self):
                return True

        saved, main.busy = main.busy, Locked()
        try:
            self.assertEqual(self.client.post("/infer", json={}).status_code, 503)
        finally:
            main.busy = saved


if __name__ == "__main__":
    unittest.main()
