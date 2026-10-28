"""마스크 한 장 → 형상 측정 → PASS/FAIL 판정. SAM 2 없이도 돌아가는 순수 OpenCV 부분이다."""
import os
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Thresholds:
    # 시뮬레이터 기준 보정값 (calibrate.py 결과). 실제 벨트·조명에서 다시 측정해 바꾼다.
    circ_min: float = float(os.environ.get("CIRC_MIN", "0.80"))
    solid_min: float = float(os.environ.get("SOLID_MIN", "0.975"))
    fill_min: float = float(os.environ.get("FILL_MIN", "0.95"))
    # 판정 이유를 고를 때만 쓴다. 파임·조각 결손은 0.10 이상, 직선으로 잘린 면은 0.06 이하.
    notch_min: float = float(os.environ.get("NOTCH_MIN", "0.08"))
    score_min: float = float(os.environ.get("SCORE_MIN", "0.80"))
    min_area: int = int(os.environ.get("MIN_MASK_AREA", "200"))


def clean_mask(mask: np.ndarray, box=None, pad: int = 4) -> np.ndarray:
    """박스 밖을 지우고, 가장 큰 덩어리만 남기고, 작은 구멍(초코칩 등)을 메운다."""
    m = (mask > 0).astype(np.uint8)
    if box is not None:
        h, w = m.shape
        x1, y1, x2, y2 = (int(round(v)) for v in box)
        keep = np.zeros_like(m)
        keep[max(0, y1 - pad):min(h, y2 + pad), max(0, x1 - pad):min(w, x2 + pad)] = 1
        m &= keep

    n, labels, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    if n <= 1:
        return np.zeros_like(m)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    m = (labels == largest).astype(np.uint8)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, kernel)
    # 외곽선 안쪽을 통째로 채워 남은 구멍도 없앤다.
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    filled = np.zeros_like(m)
    cv2.drawContours(filled, cnts, -1, 1, thickness=cv2.FILLED)
    return filled


def measure(mask: np.ndarray) -> dict | None:
    """외곽선에서 원형도·볼록도·원 채움률을 잰다. 마스크가 비었으면 None."""
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return None
    cnt = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(cnt)
    if area <= 0:
        return None
    perimeter = cv2.arcLength(cnt, True)
    hull_area = cv2.contourArea(cv2.convexHull(cnt))
    _, radius = cv2.minEnclosingCircle(cnt)
    defects = cv2.convexityDefects(cnt, cv2.convexHull(cnt, returnPoints=False))
    depth = defects.reshape(-1, 4)[:, 3].max() / 256 if defects is not None else 0.0
    return {
        "contour": cnt.reshape(-1, 2),
        "area": float(area),
        # 1.0 = 완전한 원. 픽셀 계단 때문에 실제 원도 0.9 안팎이 나온다.
        "circularity": float(4 * np.pi * area / perimeter**2),
        # 1.0 = 패인 곳 없음. 한쪽 파임·조각 결손에서 떨어진다.
        "solidity": float(area / hull_area) if hull_area > 0 else 0.0,
        # 외접원 대비 면적. 볼록하게 잘린 단면 절단·반쪽 파손에서 떨어진다.
        "fill": float(area / (np.pi * radius**2)) if radius > 0 else 0.0,
        # 가장 깊이 패인 곳의 깊이 / 외접원 반지름. 직선 절단면은 픽셀 계단만큼(0.02 안팎)만 나온다.
        "notch": float(depth / radius) if radius > 0 else 0.0,
    }


def decide(m: dict, th: Thresholds) -> tuple[str, str]:
    """기준 하나라도 미달이면 FAIL. 이유는 기준 순서가 아니라 모양으로 고른다.

    파임은 원 채움률도 같이 떨어뜨리고, 크게 잘린 면은 볼록도도 조금 떨어뜨려서
    어느 기준이 먼저 걸렸는지로는 불량 유형을 알 수 없다.
    """
    shape_fail = m["solidity"] < th.solid_min or m["fill"] < th.fill_min
    if not shape_fail and m["circularity"] >= th.circ_min:
        return "PASS", ""
    if m["notch"] >= th.notch_min:
        return "FAIL", "notch (bite or missing piece)"
    if shape_fail:
        return "FAIL", "straight cut or broken"
    return "FAIL", "low circularity"


def touches_border(box, width: int, height: int, margin: int = 2) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= margin or y1 <= margin or x2 >= width - 1 - margin or y2 >= height - 1 - margin


def simplify(contour: np.ndarray, eps: float = 1.0) -> np.ndarray:
    """전송량을 줄이려고 외곽선 점을 줄인다 (eps 픽셀 이내 오차)."""
    approx = cv2.approxPolyDP(contour.reshape(-1, 1, 2).astype(np.int32), eps, True).reshape(-1, 2)
    return approx if len(approx) >= 3 else contour
