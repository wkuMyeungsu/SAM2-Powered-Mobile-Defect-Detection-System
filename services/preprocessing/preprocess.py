"""프레임 전처리: 디코드 → 리사이즈 → 노이즈 제거 → 화이트밸런스 → 대비 보정 → 후보 영역 검출."""
import base64
import os
import time
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Config:
    # SAM 2 입력 기준(긴 변 1024). 작은 이미지는 키우지 않는다.
    target_size: int = int(os.environ.get("TARGET_SIZE", "1024"))
    jpeg_quality: int = int(os.environ.get("JPEG_QUALITY", "90"))
    # 화면 면적 대비 이 비율보다 작은 덩어리는 노이즈로 보고 버린다.
    min_area_ratio: float = float(os.environ.get("MIN_AREA_RATIO", "0.002"))
    max_candidates: int = int(os.environ.get("MAX_CANDIDATES", "20"))
    # 대시보드에 보여 줄 색 보정 전 이미지. resized: 리사이즈만 한 것(기본), raw: 받은 바이트 그대로, off: 보내지 않음
    original_mode: str = os.environ.get("ORIGINAL_MODE", "resized").strip().lower()

    def __post_init__(self):
        if self.original_mode not in ("resized", "raw", "off"):
            raise ValueError(f"ORIGINAL_MODE 는 resized/raw/off 중 하나여야 함: {self.original_mode!r}")


class PreprocessError(ValueError):
    pass


def decode(data: bytes) -> np.ndarray:
    if not data:
        raise PreprocessError("빈 이미지")
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise PreprocessError("이미지를 디코드할 수 없음 (JPEG/PNG만 지원)")
    return img


def resize(img: np.ndarray, target: int) -> tuple[np.ndarray, float]:
    h, w = img.shape[:2]
    scale = min(1.0, target / max(h, w))
    if scale == 1.0:
        return img, 1.0
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    return cv2.resize(img, size, interpolation=cv2.INTER_AREA), scale


def gray_world(img: np.ndarray) -> np.ndarray:
    """조명 색을 평균 회색에 맞춰 채널별 이득을 보정한다."""
    f = img.astype(np.float32)
    means = f.reshape(-1, 3).mean(axis=0)
    gain = means.mean() / np.maximum(means, 1e-6)
    return np.clip(f * gain, 0, 255).astype(np.uint8)


def clahe(img: np.ndarray) -> np.ndarray:
    """밝기(L) 채널에만 CLAHE를 적용해 색은 유지하고 국소 대비만 올린다."""
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    return cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)


def find_candidates(img: np.ndarray, cfg: Config) -> list[dict]:
    """배경과 구분되는 덩어리를 찾아 SAM 2 프롬프트(박스·점)로 쓸 수 있게 돌려준다."""
    h, w = img.shape[:2]
    gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 테두리 대부분이 흰색이면 배경이 밝은 것이므로 반전해 물체를 흰색으로 만든다.
    border = np.concatenate((mask[0], mask[-1], mask[:, 0], mask[:, -1]))
    if border.mean() > 127:
        mask = cv2.bitwise_not(mask)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = cfg.min_area_ratio * h * w
    found = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        m = cv2.moments(c)
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        found.append({
            "bbox": [x, y, x + bw, y + bh],
            "point": [round(cx, 1), round(cy, 1)],
            "area": int(area),
        })
    found.sort(key=lambda d: d["area"], reverse=True)
    return found[: cfg.max_candidates]


def encode_jpeg(img: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise PreprocessError("JPEG 인코딩 실패")
    return buf.tobytes()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def run(data: bytes, frame_id=None, cfg: Config = Config(), timestamp=None) -> dict:
    start = time.perf_counter()
    img = decode(data)
    oh, ow = img.shape[:2]

    img, scale = resize(img, cfg.target_size)
    resized = img
    img = cv2.GaussianBlur(img, (3, 3), 0)
    img = gray_world(img)
    img = clahe(img)
    candidates = find_candidates(img, cfg)

    h, w = img.shape[:2]
    out = {
        "frame_id": frame_id,
        "image": b64(encode_jpeg(img, cfg.jpeg_quality)),
        "format": "jpeg",
        "width": w,
        "height": h,
        "original_width": ow,
        "original_height": oh,
        "scale": scale,
        "candidates": candidates,
    }
    # 색 보정한 image 는 SAM 2 용이고, 대시보드에는 실제 색을 보여 주려고 보정 전 이미지를 함께 넘긴다.
    # inference 의 contour 는 0~1 좌표라 원본 크기가 달라도 visualizer 가 맞춰 그린다.
    if cfg.original_mode == "resized":
        out["original_image"] = b64(encode_jpeg(resized, cfg.jpeg_quality))
    elif cfg.original_mode == "raw":
        out["original_image"] = b64(data)
    if timestamp is not None:
        out["timestamp"] = timestamp
    out["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return out
