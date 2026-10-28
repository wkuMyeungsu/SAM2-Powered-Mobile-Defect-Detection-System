"""SAM 2 박스 프롬프트 분할. 모델은 프로세스당 한 번만 읽는다."""
import os

import numpy as np

MODEL_ID = os.environ.get("SAM2_MODEL", "facebook/sam2.1-hiera-tiny")


def pick_device() -> str:
    import torch

    want = os.environ.get("DEVICE", "auto")
    if want != "auto":
        return want
    return "cuda" if torch.cuda.is_available() else "cpu"


class Sam2Segmenter:
    def __init__(self, model_id: str = MODEL_ID, device: str | None = None):
        import torch
        from sam2.sam2_image_predictor import SAM2ImagePredictor

        self.torch = torch
        self.device = device or pick_device()
        self.predictor = SAM2ImagePredictor.from_pretrained(model_id, device=self.device)

    def segment(self, rgb: np.ndarray, boxes: list[list[float]]) -> tuple[np.ndarray, np.ndarray]:
        """RGB 이미지와 박스 N개 → (마스크 N×H×W bool, 점수 N).

        set_image 는 프레임당 한 번만 하고, 박스 N개는 한 번에 넣는다.
        """
        if not boxes:
            return np.zeros((0, *rgb.shape[:2]), bool), np.zeros(0, np.float32)
        with self.torch.inference_mode():
            self.predictor.set_image(rgb)
            masks, scores, _ = self.predictor.predict(box=np.asarray(boxes, np.float32), multimask_output=False)
        # 박스 1개면 (1, H, W), N개면 (N, 1, H, W) 로 나온다.
        masks = np.asarray(masks).reshape(len(boxes), *rgb.shape[:2]) > 0
        scores = np.asarray(scores, np.float32).reshape(len(boxes))
        return masks, scores
