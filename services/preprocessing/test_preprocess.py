import base64
import unittest

import cv2
import numpy as np

import preprocess


def synthetic_frame(w=1920, h=1080):
    """밝은 배경 위에 동그란 과자 2개(하나는 깨짐)를 그린 테스트 프레임."""
    img = np.full((h, w, 3), (210, 215, 220), np.uint8)
    cv2.circle(img, (600, 540), 200, (40, 90, 160), -1)
    cv2.circle(img, (1300, 540), 180, (40, 90, 160), -1)
    cv2.rectangle(img, (1300, 360), (1500, 540), (210, 215, 220), -1)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()


class PreprocessTest(unittest.TestCase):
    def test_resize_and_candidates(self):
        out = preprocess.run(synthetic_frame(), "f1")
        self.assertEqual((out["width"], out["height"]), (1024, 576))
        self.assertEqual((out["original_width"], out["original_height"]), (1920, 1080))
        self.assertAlmostEqual(out["scale"], 1024 / 1920)
        self.assertEqual(out["frame_id"], "f1")
        self.assertEqual(len(out["candidates"]), 2)
        for c in out["candidates"]:
            x1, y1, x2, y2 = c["bbox"]
            self.assertTrue(x1 <= c["point"][0] <= x2 and y1 <= c["point"][1] <= y2)

    def test_small_image_not_upscaled(self):
        ok, buf = cv2.imencode(".png", np.zeros((300, 400, 3), np.uint8))
        out = preprocess.run(buf.tobytes())
        self.assertEqual((out["width"], out["height"], out["scale"]), (400, 300, 1.0))

    def test_original_and_timestamp(self):
        out = preprocess.run(synthetic_frame(), "f2", timestamp=1761600000.5)
        self.assertEqual(out["timestamp"], 1761600000.5)
        orig = cv2.imdecode(np.frombuffer(base64.b64decode(out["original_image"]), np.uint8), cv2.IMREAD_COLOR)
        # 리사이즈만 하고 색 보정은 하지 않았으므로 배경색이 그대로다.
        self.assertEqual(orig.shape[:2], (out["height"], out["width"]))
        self.assertTrue(np.allclose(orig[10, 10], (210, 215, 220), atol=4))

        self.assertNotIn("timestamp", preprocess.run(synthetic_frame()))
        raw = synthetic_frame()
        out = preprocess.run(raw, cfg=preprocess.Config(original_mode="raw"))
        self.assertEqual(base64.b64decode(out["original_image"]), raw)
        out = preprocess.run(raw, cfg=preprocess.Config(original_mode="off"))
        self.assertNotIn("original_image", out)
        with self.assertRaises(ValueError):
            preprocess.Config(original_mode="nope")

    def test_box_pad(self):
        base = preprocess.run(synthetic_frame())["candidates"]
        padded = preprocess.run(synthetic_frame(), cfg=preprocess.Config(box_pad=0.1))["candidates"]
        self.assertEqual(len(base), len(padded))
        for b, p in zip(base, padded):
            bw = b["bbox"][2] - b["bbox"][0]
            self.assertAlmostEqual(p["bbox"][0], b["bbox"][0] - round(bw * 0.1), delta=1)
            self.assertGreater(p["bbox"][2], b["bbox"][2])
            self.assertEqual(b["point"], p["point"])  # 중심점은 그대로

    def test_box_pad_clipped_to_frame(self):
        img = np.full((400, 400, 3), 220, np.uint8)
        cv2.circle(img, (30, 200), 60, (40, 90, 160), -1)  # 왼쪽 테두리에 걸친 과자
        ok, buf = cv2.imencode(".png", img)
        out = preprocess.run(buf.tobytes(), cfg=preprocess.Config(box_pad=0.3))
        x1, y1, x2, y2 = out["candidates"][0]["bbox"]
        self.assertTrue(x1 >= 0 and y1 >= 0 and x2 <= 400 and y2 <= 400)

    def test_roi(self):
        # 오른쪽 과자(지름 360px, 중심 x≈1300/1920=0.68) 쪽만 보도록 ROI 를 잡는다.
        cfg = preprocess.Config(roi="0.5,0.1,1.0,0.9")
        out = preprocess.run(synthetic_frame(), cfg=cfg)
        self.assertEqual(len(out["candidates"]), 1)
        c = out["candidates"][0]
        self.assertGreater(c["point"][0], 0.5 * out["width"])  # 좌표는 화면 전체 기준
        full = [d for d in preprocess.run(synthetic_frame())["candidates"] if d["point"][0] > 0.5 * out["width"]][0]
        self.assertEqual(c["bbox"], full["bbox"])

    def test_bad_settings(self):
        for kw in ({"roi": "0.5,0.5,0.2,0.9"}, {"roi": "a,b"}, {"roi": "0,0,1.5,1"},
                   {"white_balance": "maybe"}, {"box_pad": -0.1}, {"box_pad": 0.9}):
            with self.assertRaises(ValueError, msg=kw):
                preprocess.Config(**kw)

    def test_white_balance_off(self):
        out = preprocess.run(synthetic_frame(), cfg=preprocess.Config(white_balance="off"))
        self.assertEqual(len(out["candidates"]), 2)

    def test_debug_image(self):
        debug = []
        out = preprocess.run(synthetic_frame(), debug=debug, cfg=preprocess.Config(roi="0.1,0.1,0.9,0.9"))
        self.assertEqual(debug[0].shape[:2], (out["height"], out["width"]))

    def test_invalid_image(self):
        with self.assertRaises(preprocess.PreprocessError):
            preprocess.run(b"not an image")
        with self.assertRaises(preprocess.PreprocessError):
            preprocess.run(b"")


if __name__ == "__main__":
    unittest.main()
