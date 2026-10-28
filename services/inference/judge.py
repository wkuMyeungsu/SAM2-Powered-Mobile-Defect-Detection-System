"""마스크 한 장 → 형상 측정 → PASS/FAIL 판정. SAM 2 없이도 돌아가는 순수 OpenCV 부분이다."""
import os
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class Thresholds:
    # 카메라로 찍으면 원근 때문에 원이 타원이 되고 그림자·흐림이 섞인다. 그래서 판정은 원에서 얼마나
    # 벗어났는지(채움률·원형도)가 아니라 원근에 덜 민감한 두 특징으로 한다 (calibrate.py 로 보정).
    # 패인 깊이: 파임·조각 결손 0.10 이상, 정상·직선 절단면 0.06 이하.
    notch_min: float = float(os.environ.get("NOTCH_MIN", "0.08"))
    # 가장 긴 직선 변: 단면 절단·반쪽 파손 0.9 이상, 정상 0.65 이하 (타원이 돼도 직선은 안 생긴다).
    straight_min: float = float(os.environ.get("STRAIGHT_MIN", "0.85"))
    # 아래 셋은 크게 부서진 경우만 잡는 안전장치. 정상 쿠키가 15% 납작해져도 걸리지 않게 낮게 둔다.
    fill_min: float = float(os.environ.get("FILL_MIN", "0.75"))
    solid_min: float = float(os.environ.get("SOLID_MIN", "0.95"))
    circ_min: float = float(os.environ.get("CIRC_MIN", "0.70"))
    # 쿠키 색 (OpenCV HSV, H 0~180). 화면 글자·벨트 무늬 같은 후보를 버린다. COOKIE_SAT_MIN=0 이면 끈다.
    hue_min: float = float(os.environ.get("COOKIE_HUE_MIN", "5"))
    hue_max: float = float(os.environ.get("COOKIE_HUE_MAX", "30"))
    sat_min: float = float(os.environ.get("COOKIE_SAT_MIN", "60"))
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
    """외곽선에서 형상 특징을 잰다. 마스크가 비었으면 None."""
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
        # 가장 긴 직선 변 / 외접원 반지름. 잘린 면은 0.9~2.0, 원·타원 둘레는 0.65 이하.
        "straight": longest_straight(cnt, radius) / radius if radius > 0 else 0.0,
    }


def longest_straight(cnt: np.ndarray, radius: float, max_turn_deg: float = 8) -> float:
    """외곽선을 반지름의 2% 오차로 다각형 근사했을 때 가장 긴 변의 길이.

    근사가 직선 변 한가운데를 꼭짓점으로 고르기도 해서, 거의 꺾이지 않는(max_turn_deg 미만)
    이웃 변은 하나로 합친다. 원 둘레를 같은 오차로 근사하면 이웃 변이 20도 넘게 꺾인다.
    """
    c = cnt.reshape(-1, 2)
    # 닫힌 곡선의 시작점에서 직선이 끊기지 않게 두 바퀴를 열린 곡선으로 근사한다.
    loop = np.concatenate([c, c]).reshape(-1, 1, 2).astype(np.int32)
    pts = cv2.approxPolyDP(loop, max(1.0, 0.02 * radius), False).reshape(-1, 2).astype(np.float32)
    if len(pts) < 2:
        return 0.0
    d = np.diff(pts, axis=0)
    ang = np.arctan2(d[:, 1], d[:, 0])
    turn = np.abs((np.diff(ang) + np.pi) % (2 * np.pi) - np.pi)
    best, start = 0.0, 0
    for k in range(len(d)):
        if k == len(d) - 1 or turn[k] >= np.radians(max_turn_deg):
            best = max(best, float(np.linalg.norm(pts[k + 1] - pts[start])))
            start = k + 1
    return best


def looks_like_cookie(bgr: np.ndarray, mask: np.ndarray, th: Thresholds) -> bool:
    """마스크 안 색의 중앙값이 쿠키 색(주황~갈색, 채도 있음)인지 본다."""
    if th.sat_min <= 0:
        return True
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)[mask > 0]
    if len(hsv) == 0:
        return False
    h, s = np.median(hsv[:, 0]), np.median(hsv[:, 1])
    return th.hue_min <= h <= th.hue_max and s >= th.sat_min


def decide(m: dict, th: Thresholds) -> tuple[str, str]:
    """패인 곳이 있거나 직선으로 잘린 변이 있으면 FAIL. 나머지 기준은 크게 부서진 경우의 안전장치."""
    if m["notch"] >= th.notch_min:
        return "FAIL", "notch (bite or missing piece)"
    if m["straight"] >= th.straight_min or m["fill"] < th.fill_min:
        return "FAIL", "straight cut or broken"
    if m["solidity"] < th.solid_min:
        return "FAIL", "dented outline"
    if m["circularity"] < th.circ_min:
        return "FAIL", "low circularity"
    return "PASS", ""


def touches_border(box, width: int, height: int, margin: int = 2) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= margin or y1 <= margin or x2 >= width - 1 - margin or y2 >= height - 1 - margin


def simplify(contour: np.ndarray, eps: float = 1.0) -> np.ndarray:
    """전송량을 줄이려고 외곽선 점을 줄인다 (eps 픽셀 이내 오차)."""
    approx = cv2.approxPolyDP(contour.reshape(-1, 1, 2).astype(np.int32), eps, True).reshape(-1, 2)
    return approx if len(approx) >= 3 else contour
