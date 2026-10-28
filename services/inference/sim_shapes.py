"""conveyor-cookie-sim.html 의 makeShape() 를 그대로 옮긴 것. 판정 기준값 보정과 테스트에 쓴다."""
import math
import random

import cv2
import numpy as np

SEG = 180
TYPES = ["bite", "cut", "wedge", "half"]


def make_shape(defect_type: str, strength: float = 55, rng: random.Random = random) -> list[float]:
    """반지름 1 기준 각도별 반지름(SEG개). defect_type 이 'round' 면 정상품."""
    s = strength / 100
    direction = rng.random() * math.pi * 2
    amp = 0.55 + rng.random() * 0.45
    k = s * amp

    bite_r = 0.35 + k * 0.55
    bite_d = 1 + bite_r * (0.62 - k * 0.34)
    cut_d = 0.86 - k * 0.52
    half_d = 0.17 - k * 0.15
    wedge_w = 0.22 + k * 0.5

    rad = []
    for i in range(SEG):
        a = i / SEG * math.pi * 2
        d = ((a - direction + math.pi * 3) % (math.pi * 2)) - math.pi
        c = math.cos(d)
        r = 1.0
        if defect_type == "bite":
            b = bite_d * c
            disc = b * b - (bite_d * bite_d - bite_r * bite_r)
            if disc > 0:
                t = b - math.sqrt(disc)
                if 0 < t < 1:
                    r = t
        elif defect_type in ("cut", "half"):
            D = cut_d if defect_type == "cut" else half_d
            if c > 0.001:
                r = min(1.0, D / c)
        elif defect_type == "wedge":
            if abs(d) < wedge_w:
                r = 0.07 + abs(d) / wedge_w * 0.1
        rad.append(max(0.06, r))
    return rad


def polygon(rad: list[float], cx: float, cy: float, radius: float, rot: float) -> np.ndarray:
    pts = []
    for i, rr in enumerate(rad):
        a = i / SEG * math.pi * 2 + rot
        pts.append((cx + math.cos(a) * radius * rr, cy + math.sin(a) * radius * rr))
    return np.array(pts, np.float32)


def render_mask(defect_type: str, diameter: float = 78, strength: float = 55, rng: random.Random = random) -> np.ndarray:
    """과자 하나를 픽셀 마스크로 그린다 (SAM 2 가 돌려줄 마스크와 같은 계단 모양)."""
    size = int(diameter * 1.4)
    c = size / 2 + rng.random() - 0.5
    rot = rng.random() * math.pi * 2
    mask = np.zeros((size, size), np.uint8)
    if defect_type == "round":
        # 시뮬레이터도 정상품은 ctx.arc 로 그린다.
        cv2.circle(mask, (round(c * 16), round(c * 16)), round(diameter / 2 * 16), 1, cv2.FILLED, cv2.LINE_8, shift=4)
    else:
        pts = polygon(make_shape(defect_type, strength, rng), c, c, diameter / 2, rot)
        cv2.fillPoly(mask, [np.round(pts * 16).astype(np.int32)], 1, cv2.LINE_8, shift=4)
    return mask
