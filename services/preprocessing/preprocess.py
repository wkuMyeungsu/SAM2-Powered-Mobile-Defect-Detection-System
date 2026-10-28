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
    # 찾은 박스를 가로·세로 크기의 이 비율만큼 사방으로 넓힌다. SAM 2 가 과자 가장자리를 잘라 먹지 않게 한다.
    box_pad: float = float(os.environ.get("BOX_PAD", "0"))
    # 과자를 찾을 영역 "x1,y1,x2,y2" (0~1 비율). 비우면 화면 전체. 예: "0.1,0.2,0.9,0.8"
    roi: str = os.environ.get("ROI", "").strip()
    # gray-world 화이트밸런스. 과자가 화면을 많이 차지해 색이 출렁이면 off
    white_balance: str = os.environ.get("WHITE_BALANCE", "on").strip().lower()

    def __post_init__(self):
        if self.original_mode not in ("resized", "raw", "off"):
            raise ValueError(f"ORIGINAL_MODE 는 resized/raw/off 중 하나여야 함: {self.original_mode!r}")
        if self.white_balance not in ("on", "off"):
            raise ValueError(f"WHITE_BALANCE 는 on/off 중 하나여야 함: {self.white_balance!r}")
        if not 0 <= self.box_pad <= 0.5:
            raise ValueError(f"BOX_PAD 는 0~0.5 여야 함: {self.box_pad}")
        self.roi_box()  # 형식 확인

    def roi_box(self):
        """ROI 를 (x1, y1, x2, y2) 비율로. 없으면 None."""
        if not self.roi:
            return None
        try:
            x1, y1, x2, y2 = (float(v) for v in self.roi.split(","))
        except ValueError:
            raise ValueError(f'ROI 는 "x1,y1,x2,y2" (0~1) 형식이어야 함: {self.roi!r}')
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError(f"ROI 는 0 <= x1 < x2 <= 1, 0 <= y1 < y2 <= 1 이어야 함: {self.roi!r}")
        return x1, y1, x2, y2


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


def roi_pixels(cfg: Config, w: int, h: int) -> tuple[int, int, int, int]:
    r = cfg.roi_box()
    if r is None:
        return 0, 0, w, h
    x1, y1, x2, y2 = round(r[0] * w), round(r[1] * h), round(r[2] * w), round(r[3] * h)
    return x1, y1, max(x2, x1 + 1), max(y2, y1 + 1)


def find_candidates(img: np.ndarray, cfg: Config) -> list[dict]:
    """배경과 구분되는 덩어리를 찾아 SAM 2 프롬프트(박스·점)로 쓸 수 있게 돌려준다.

    좌표는 항상 img 전체 기준이다. ROI 가 있으면 그 안에서만 찾고 좌표를 되돌려 놓는다.
    """
    h, w = img.shape[:2]
    rx1, ry1, rx2, ry2 = roi_pixels(cfg, w, h)
    crop = img[ry1:ry2, rx1:rx2]
    ch, cw = crop.shape[:2]

    gray = cv2.GaussianBlur(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 테두리 대부분이 흰색이면 배경이 밝은 것이므로 반전해 물체를 흰색으로 만든다.
    border = np.concatenate((mask[0], mask[-1], mask[:, 0], mask[:, -1]))
    if border.mean() > 127:
        mask = cv2.bitwise_not(mask)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    # 최소 면적은 ROI 와 상관없이 화면 전체 기준이라 ROI 를 바꿔도 같은 과자는 같게 걸러진다.
    min_area = cfg.min_area_ratio * h * w
    found = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area:
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        m = cv2.moments(c)
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        # 박스 여유: 화면 밖으로는 넓히지 않는다.
        px, py = round(bw * cfg.box_pad), round(bh * cfg.box_pad)
        x1, y1 = max(0, rx1 + x - px), max(0, ry1 + y - py)
        x2, y2 = min(w, rx1 + x + bw + px), min(h, ry1 + y + bh + py)
        found.append({
            "bbox": [x1, y1, x2, y2],
            "point": [round(rx1 + cx, 1), round(ry1 + cy, 1)],
            "area": int(area),
        })
    found.sort(key=lambda d: d["area"], reverse=True)
    return found[: cfg.max_candidates]


def draw_debug(img: np.ndarray, candidates: list[dict], cfg: Config) -> np.ndarray:
    """ROI(파랑)와 찾은 박스(초록)를 그린 확인용 이미지."""
    out = img.copy()
    h, w = out.shape[:2]
    if cfg.roi_box() is not None:
        x1, y1, x2, y2 = roi_pixels(cfg, w, h)
        cv2.rectangle(out, (x1, y1), (x2 - 1, y2 - 1), (255, 128, 0), 2)
    for i, c in enumerate(candidates):
        x1, y1, x2, y2 = c["bbox"]
        cv2.rectangle(out, (x1, y1), (x2 - 1, y2 - 1), (0, 220, 0), 2)
        cv2.circle(out, (round(c["point"][0]), round(c["point"][1])), 4, (0, 0, 255), -1)
        cv2.putText(out, str(i), (x1 + 3, y1 + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 220, 0), 2)
    cv2.putText(out, f"candidates: {len(candidates)}", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    return out


def encode_jpeg(img: np.ndarray, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise PreprocessError("JPEG 인코딩 실패")
    return buf.tobytes()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def run(data: bytes, frame_id=None, cfg: Config = Config(), timestamp=None, debug: list | None = None) -> dict:
    """debug 에 리스트를 넘기면 확인용 이미지(색 보정 전 프레임 + ROI·박스)를 하나 넣어 준다."""
    start = time.perf_counter()
    img = decode(data)
    oh, ow = img.shape[:2]

    img, scale = resize(img, cfg.target_size)
    resized = img
    img = cv2.GaussianBlur(img, (3, 3), 0)
    if cfg.white_balance == "on":
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
    if debug is not None:
        debug.append(draw_debug(resized, candidates, cfg))
    return out
