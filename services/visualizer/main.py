"""C4 Visualizer (port 8004)

inference 가 보낸 판정 결과를 원본 프레임 위에 그린 뒤 gateway 로 돌려보낸다.
입출력 형식은 contracts/inference-visualizer.md, contracts/visualizer-gateway.md 참고.
"""
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import cv2
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

PORT = int(os.getenv("PORT", "8004"))
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8001/result")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))
SCAN_X = float(os.getenv("SCAN_X", "0.34"))                # 검사선 x 위치(0~1). 과자 중심이 이 선을 지날 때만 누적 집계
EDGE_MARGIN = float(os.getenv("EDGE_MARGIN", "0.005"))     # 화면 테두리에 닿은 과자는 잘려 보이는 것이므로 WAIT 처리
MAX_JUMP = float(os.getenv("MAX_JUMP", "0.15"))            # 두 프레임 사이 과자가 움직일 수 있는 최대 거리(0~1)
SAME_LINE_TOL = float(os.getenv("SAME_LINE_TOL", "0.05"))  # 같은 과자로 볼 y 차이 한계(0~1)
RESET_GAP = int(os.getenv("RESET_GAP", "300"))             # frame_id 가 이만큼 작아지면 gateway 재시작으로 보고 초기화

COLORS = {"PASS": (0, 200, 0), "FAIL": (0, 0, 255), "WAIT": (160, 160, 160)}  # BGR
SCAN_COLOR = (69, 90, 255)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("visualizer")


class DetectedObject(BaseModel):
    contour: list[list[float]] = Field(description="과자 윤곽선 [[x, y], ...], 0~1 정규화 좌표")
    circularity: float
    result: Literal["PASS", "FAIL", "WAIT"]


class InferenceResult(BaseModel):
    frame_id: int
    timestamp: float = Field(description="gateway 가 프레임을 받은 시각 (unix seconds)")
    image: str = Field(description="원본 프레임 JPEG/PNG base64")
    objects: list[DetectedObject] = []


@dataclass
class Tracked:
    cx: float
    cy: float
    result: str


@dataclass
class State:
    last_id: int = -1
    prev: list[Tracked] = field(default_factory=list)
    passed_total: int = 0
    defect_total: int = 0
    latest_jpeg: bytes | None = None


state = State()


def decode_image(b64: str) -> np.ndarray:
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        buf = np.frombuffer(base64.b64decode(b64), np.uint8)
    except ValueError:
        raise HTTPException(400, "image 가 올바른 base64 가 아닙니다")
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise HTTPException(400, "image 디코딩 실패 (JPEG/PNG 만 지원)")
    return img


def touches_edge(pts: np.ndarray) -> bool:
    return bool((pts.min(axis=0) <= EDGE_MARGIN).any() or (pts.max(axis=0) >= 1 - EDGE_MARGIN).any())


def centroid(pts: np.ndarray) -> tuple[float, float]:
    m = cv2.moments(pts.astype(np.float32))
    if m["m00"] == 0:
        cx, cy = pts.mean(axis=0)
        return float(cx), float(cy)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def count_crossings(current: list[Tracked]) -> None:
    """직전 프레임에서 검사선 오른쪽에 있던 과자가 이번 프레임에 왼쪽으로 넘어왔으면 1개로 센다.

    같은 과자가 여러 프레임에 찍혀도 검사선을 넘는 순간은 한 번뿐이라 중복 집계가 없다.
    """
    used = set()
    for cur in current:
        if cur.result == "WAIT" or cur.cx >= SCAN_X:
            continue
        best = None
        for i, p in enumerate(state.prev):
            if i in used or p.cx < SCAN_X:
                continue
            dx = p.cx - cur.cx
            if 0 < dx <= MAX_JUMP and abs(p.cy - cur.cy) <= SAME_LINE_TOL and (best is None or dx < best[1]):
                best = (i, dx)
        if best:
            used.add(best[0])
            state.passed_total += 1
            state.defect_total += cur.result == "FAIL"
    state.prev = current


def put_label(img: np.ndarray, text: str, org: tuple[int, int], color, scale: float, thick: int) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw(img: np.ndarray, items: list[tuple[np.ndarray, str, float]], hud: list[str]) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max(0.4, w / 1800)
    thick = max(1, round(w / 600))

    polys = [(np.round(pts * [w, h]).astype(np.int32), result, circ) for pts, result, circ in items if len(pts) >= 3]

    # 불량 영역은 반투명으로 채운다
    overlay = img.copy()
    for px, result, _ in polys:
        if result == "FAIL":
            cv2.fillPoly(overlay, [px], COLORS["FAIL"])
    img = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)

    sx = int(SCAN_X * w)
    cv2.line(img, (sx, 0), (sx, h), SCAN_COLOR, 1, cv2.LINE_AA)

    for px, result, circ in polys:
        color = COLORS[result]
        cv2.polylines(img, [px], True, color, thick + 1, cv2.LINE_AA)
        text = f"{result} {circ:.2f}"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
        x0, y0 = px.min(axis=0)
        x = min(max(int(x0), 2), w - tw - 2)  # 화면 밖으로 글자가 잘리지 않게
        y = y0 - 6 if y0 - 6 > th else px[:, 1].max() + th + 6
        put_label(img, text, (x, int(y)), color, scale, thick)

    line_h = int(26 * scale)
    cv2.rectangle(img, (0, 0), (int(330 * scale), line_h * len(hud) + 8), (0, 0, 0), -1)
    for i, line in enumerate(hud):
        put_label(img, line, (6, line_h * (i + 1)), (255, 255, 255), scale * 0.8, thick)
    return img


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=2.0)
    log.info("gateway=%s scan_x=%.2f", GATEWAY_URL, SCAN_X)
    yield
    await app.state.client.aclose()


app = FastAPI(title="C4 Visualizer", lifespan=lifespan)


@app.post("/visualize")
async def visualize(f: InferenceResult):
    # inference 를 여러 대 띄우면 결과가 순서 없이 도착한다. 이미 보낸 것보다 오래된 프레임은 버려야 영상이 뒤로 튀지 않는다.
    if f.frame_id <= state.last_id:
        if state.last_id - f.frame_id < RESET_GAP:
            return {"status": "dropped", "frame_id": f.frame_id, "last_sent": state.last_id}
        log.warning("frame_id %d -> %d: gateway 재시작으로 보고 상태 초기화", state.last_id, f.frame_id)
        reset()

    img = decode_image(f.image)
    state.last_id = f.frame_id

    items, tracked = [], []
    for o in f.objects:
        pts = np.asarray(o.contour, dtype=np.float32).reshape(-1, 2)
        if len(pts) < 3:
            continue
        result = "WAIT" if touches_edge(pts) else o.result
        cx, cy = centroid(pts)
        items.append((pts, result, o.circularity))
        tracked.append(Tracked(cx, cy, result))
    count_crossings(tracked)

    latency_ms = int((time.time() - f.timestamp) * 1000)
    defect_in_frame = sum(t.result == "FAIL" for t in tracked)
    hud = [
        f"FRAME {f.frame_id}  {latency_ms} ms",
        f"IN FRAME {len(tracked)}  NG {defect_in_frame}",
        f"PASSED {state.passed_total}  NG {state.defect_total}",
    ]
    img = draw(img, items, hud)

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise HTTPException(500, "JPEG 인코딩 실패")
    state.latest_jpeg = buf.tobytes()

    payload = {
        "frame_id": f.frame_id,
        "timestamp": f.timestamp,
        "latency_ms": latency_ms,
        "image": base64.b64encode(state.latest_jpeg).decode(),
        "summary": {
            "in_frame": len(tracked),
            "defect_in_frame": defect_in_frame,
            "passed_total": state.passed_total,
            "defect_total": state.defect_total,
        },
        "objects": [
            {"id": i, "result": result, "circularity": round(circ, 4)}
            for i, (_, result, circ) in enumerate(items)
        ],
    }
    try:
        r = await app.state.client.post(GATEWAY_URL, json=payload)
        r.raise_for_status()
    except httpx.HTTPError as e:
        # gateway 가 죽어 있어도 inference 쪽에는 정상 응답한다. 재전송하면 지연만 쌓인다.
        log.warning("gateway 전송 실패 (frame %d): %r", f.frame_id, e)
        return {"status": "gateway_error", "frame_id": f.frame_id}
    return {"status": "ok", "frame_id": f.frame_id}


@app.post("/reset")
def reset():
    """누적 집계와 frame_id 기록을 초기화한다. 시연을 새로 시작할 때 호출."""
    global state
    state = State()
    return {"status": "ok"}


@app.get("/latest.jpg")
def latest():
    """마지막으로 그린 프레임. gateway 없이 브라우저로 결과를 확인할 때 쓴다."""
    if state.latest_jpeg is None:
        raise HTTPException(404, "아직 처리한 프레임이 없습니다")
    return Response(state.latest_jpeg, media_type="image/jpeg")


@app.get("/health")
def health():
    return {
        "service": os.getenv("SERVICE_NAME", "visualizer"),
        "status": "ok",
        "last_frame_id": state.last_id,
        "passed_total": state.passed_total,
        "defect_total": state.defect_total,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
