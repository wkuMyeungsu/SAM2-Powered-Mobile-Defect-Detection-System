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

    def test_invalid_image(self):
        with self.assertRaises(preprocess.PreprocessError):
            preprocess.run(b"not an image")
        with self.assertRaises(preprocess.PreprocessError):
            preprocess.run(b"")


if __name__ == "__main__":
    unittest.main()
