"""C4 Visualizer (port 8004)

inference 가 보낸 판정 결과를 원본 프레임 위에 그린 뒤 gateway 로 돌려보낸다.
입출력 형식은 contracts/inference-visualizer.md, contracts/visualizer-gateway.md 참고.
"""
import asyncio
import base64
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import cv2
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

PORT = int(os.getenv("PORT", "8004"))
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://gateway:8001/result")
JPEG_QUALITY = int(os.getenv("JPEG_QUALITY", "80"))
SCAN_X = float(os.getenv("SCAN_X", "0.34"))                # 검사선 x 위치(0~1). 과자 중심이 이 선을 지날 때만 누적 집계
EDGE_MARGIN = float(os.getenv("EDGE_MARGIN", "0.005"))     # 화면 테두리에 닿은 과자는 잘려 보이는 것이므로 WAIT 처리
MAX_JUMP = float(os.getenv("MAX_JUMP", "0.15"))            # 두 프레임 사이 과자가 움직일 수 있는 최대 거리(0~1)
JITTER = float(os.getenv("JITTER", "0.03"))                # 마스크가 흔들려 과자 중심이 뒤로 갈 수 있는 거리(0~1)
SAME_LINE_TOL = float(os.getenv("SAME_LINE_TOL", "0.05"))  # 같은 과자로 볼 y 차이 한계(0~1)
RESET_GAP = int(os.getenv("RESET_GAP", "300"))             # frame_id 가 이만큼 작아지면 gateway 재시작으로 보고 초기화
SEND_QUEUE = int(os.getenv("SEND_QUEUE", "3"))             # gateway 전송 대기열 크기. 넘치면 가장 오래된 프레임부터 버림
MAX_BODY = int(os.getenv("MAX_BODY_BYTES", str(10 * 1024 * 1024)))
METRICS_WINDOW = float(os.getenv("METRICS_WINDOW", "10"))  # /metrics 의 fps·지연시간을 계산할 최근 구간(초)
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")    # web(8000) 대시보드가 /reset, /metrics 를 부를 수 있게

COLORS = {"PASS": (0, 200, 0), "FAIL": (0, 0, 255), "WAIT": (160, 160, 160)}  # BGR
SCAN_COLOR = (69, 90, 255)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("visualizer")


class DetectedObject(BaseModel):
    contour: list[list[float]] = Field(description="과자 윤곽선 [[x, y], ...]. 0~1 정규화 좌표 권장, 픽셀 좌표도 받음")
    circularity: float
    result: Literal["PASS", "FAIL", "WAIT"]


class InferenceResult(BaseModel):
    frame_id: int | str = Field(description="gateway 가 붙인 번호. 숫자 또는 숫자 문자열이면 순서 정리에 쓴다")
    timestamp: float | None = Field(None, description="gateway 가 프레임을 받은 시각 (unix seconds). 없으면 지연시간 생략")
    image: str = Field(description="프레임 JPEG/PNG base64")
    width: int | None = Field(None, description="contour 가 픽셀 좌표일 때 그 좌표의 기준 이미지 너비. 없으면 image 크기")
    height: int | None = Field(None, description="contour 가 픽셀 좌표일 때 그 좌표의 기준 이미지 높이. 없으면 image 크기")
    objects: list[DetectedObject] = []


@dataclass
class Tracked:
    cx: float
    cy: float
    result: str
    counted: bool = False
    tid: int = -1  # 과자 고정 번호. 프레임이 바뀌어도 같은 과자면 같은 번호


@dataclass
class State:
    last_id: int = -1      # 순서 확인을 통과한 마지막 frame_id
    last_queued: int = -1  # gateway 전송 대기열에 넣은 마지막 frame_id
    prev: list[Tracked] = field(default_factory=list)
    next_tid: int = 1
    passed_total: int = 0
    defect_total: int = 0
    latest_jpeg: bytes | None = None


@dataclass
class Metrics:
    started: float = field(default_factory=time.time)
    received: int = 0
    processed: int = 0
    dropped_old: int = 0
    sent: int = 0
    send_errors: int = 0
    send_skipped: int = 0
    recent: deque = field(default_factory=lambda: deque(maxlen=2000))  # (완료 시각, 지연 ms 또는 None, 그리기 ms)


state = State()
metrics = Metrics()


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


def order_key(frame_id: int | str) -> int | None:
    """순서 비교용 정수. preprocessing 처럼 "123" 문자열로 와도 비교할 수 있게 한다."""
    try:
        return int(frame_id)
    except (TypeError, ValueError):
        return None


def touches_edge(pts: np.ndarray) -> bool:
    return bool((pts.min(axis=0) <= EDGE_MARGIN).any() or (pts.max(axis=0) >= 1 - EDGE_MARGIN).any())


def centroid(pts: np.ndarray) -> tuple[float, float]:
    m = cv2.moments(pts.astype(np.float32))
    if m["m00"] == 0:
        cx, cy = pts.mean(axis=0)
        return float(cx), float(cy)
    return m["m10"] / m["m00"], m["m01"] / m["m00"]


def track(current: list[Tracked]) -> None:
    """직전 프레임의 과자와 짝을 지어 고정 번호를 이어 붙이고, 검사선을 처음 넘는 순간에만 1개로 센다.

    짝은 같은 줄에서 왼쪽으로 조금 움직인(또는 JITTER 만큼 흔들린) 가장 가까운 과자다.
    센 표시를 짝에게 물려주므로, 마스크가 흔들려 과자 중심이 검사선을 넘나들어도 다시 세지 않는다.
    짝이 없는 과자(새로 화면에 들어온 과자)는 새 번호를 받는다.
    """
    pairs = sorted(
        (abs(p.cx - c.cx), i, j)
        for i, c in enumerate(current)
        for j, p in enumerate(state.prev)
        if -JITTER <= p.cx - c.cx <= MAX_JUMP and abs(p.cy - c.cy) <= SAME_LINE_TOL
    )
    used_cur, used_prev = set(), set()
    for _, i, j in pairs:
        if i in used_cur or j in used_prev:
            continue
        used_cur.add(i)
        used_prev.add(j)
        c, p = current[i], state.prev[j]
        c.counted = p.counted
        c.tid = p.tid
        if not c.counted and c.result != "WAIT" and p.cx >= SCAN_X > c.cx:
            c.counted = True
            state.passed_total += 1
            state.defect_total += c.result == "FAIL"
    for c in current:
        if c.tid < 0:
            c.tid = state.next_tid
            state.next_tid += 1
    state.prev = current


def put_label(img: np.ndarray, text: str, org: tuple[int, int], color, scale: float, thick: int) -> None:
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def tint(img: np.ndarray, px: np.ndarray, color, alpha: float) -> None:
    """윤곽선 안쪽을 color 로 반투명하게 칠한다 (img 를 직접 바꾼다)"""
    h, w = img.shape[:2]
    x, y, bw, bh = cv2.boundingRect(px)
    x0, y0, x1, y1 = max(x, 0), max(y, 0), min(x + bw, w), min(y + bh, h)
    if x1 <= x0 or y1 <= y0:
        return
    roi = img[y0:y1, x0:x1]
    mask = np.zeros(roi.shape[:2], np.uint8)
    cv2.fillPoly(mask, [px - [x0, y0]], 255)
    tinted = cv2.addWeighted(roi, 1 - alpha, np.full_like(roi, color), alpha, 0)
    cv2.copyTo(tinted, mask, roi)


def draw(img: np.ndarray, items: list[tuple[np.ndarray, str, float, int]], hud: list[str]) -> np.ndarray:
    h, w = img.shape[:2]
    scale = max(0.4, w / 1800)
    thick = max(1, round(w / 600))

    polys = [(np.round(pts * [w, h]).astype(np.int32), result, circ, tid) for pts, result, circ, tid in items]

    # 불량 영역은 반투명으로 채운다. 화면 전체가 아니라 과자를 감싸는 사각형 안만 합성해야 빠르다
    for px, result, _, _ in polys:
        if result == "FAIL":
            tint(img, px, COLORS["FAIL"], 0.35)

    sx = int(SCAN_X * w)
    cv2.line(img, (sx, 0), (sx, h), SCAN_COLOR, 1)

    for px, result, circ, tid in polys:
        color = COLORS[result]
        cv2.polylines(img, [px], True, color, thick + 1)  # 안티앨리어싱(LINE_AA)은 5배 느리고 두께가 있어 눈으로 차이가 거의 없다
        text = f"#{tid} {result} {circ:.2f}"
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


def render_jpeg(img: np.ndarray, items: list[tuple[np.ndarray, str, float, int]], hud: list[str]) -> bytes:
    ok, buf = cv2.imencode(".jpg", draw(img, items, hud), [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise HTTPException(500, "JPEG 인코딩 실패")
    return buf.tobytes()


def enqueue(payload: dict) -> None:
    """gateway 가 느려도 inference 를 기다리게 하지 않도록 대기열에 넣고 바로 돌아간다."""
    q: asyncio.Queue = app.state.queue
    if q.full():
        q.get_nowait()  # 화면은 최신 프레임이 중요하므로 가장 오래된 것을 버린다
        metrics.send_skipped += 1
    q.put_nowait(payload)


async def send_worker() -> None:
    """대기열의 프레임을 한 번에 하나씩, 넣은 순서대로 gateway 로 보낸다."""
    q: asyncio.Queue = app.state.queue
    while True:
        payload = await q.get()
        try:
            r = await app.state.client.post(GATEWAY_URL, json=payload)
            r.raise_for_status()
            metrics.sent += 1
        except httpx.HTTPError as e:
            metrics.send_errors += 1
            if metrics.send_errors == 1 or metrics.send_errors % 100 == 0:  # gateway 가 꺼져 있을 때 로그가 넘치지 않게
                log.warning("gateway 전송 실패 %d회 (frame %s): %r", metrics.send_errors, payload["frame_id"], e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=2.0)
    app.state.queue = asyncio.Queue(SEND_QUEUE)
    worker = asyncio.create_task(send_worker())
    log.info("gateway=%s scan_x=%.2f", GATEWAY_URL, SCAN_X)
    yield
    worker.cancel()
    await app.state.client.aclose()


app = FastAPI(title="C4 Visualizer", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.middleware("http")
async def limit_body(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY:
        return JSONResponse({"error": f"본문이 너무 큽니다 (최대 {MAX_BODY} bytes)"}, status_code=413)
    return await call_next(request)


# 오류 본문은 preprocessing 과 같은 {"error": "..."} 형식으로 맞춘다
@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    e = exc.errors()[0]
    where = ".".join(str(p) for p in e["loc"][1:]) or "body"
    return JSONResponse({"error": f"{where}: {e['msg']}"}, status_code=400)


def dropped(f: InferenceResult) -> dict:
    metrics.dropped_old += 1
    return {"status": "dropped", "frame_id": f.frame_id, "last_sent": state.last_id}


@app.post("/visualize")
async def visualize(f: InferenceResult):
    metrics.received += 1
    img = await run_in_threadpool(decode_image, f.image)

    # inference 를 여러 대 띄우면 결과가 순서 없이 도착한다. 이미 보낸 것보다 오래된 프레임은 버려야 영상이 뒤로 튀지 않는다.
    # frame_id 가 숫자가 아니면 순서를 알 수 없으므로 도착한 순서대로 처리한다.
    # 확인부터 집계까지는 await 없이 이어서 실행하므로 다른 요청이 끼어들지 않는다.
    key = order_key(f.frame_id)
    if key is not None and key <= state.last_id:
        if state.last_id - key < RESET_GAP:
            return dropped(f)
        log.warning("frame_id %d -> %d: gateway 재시작으로 보고 상태 초기화", state.last_id, key)
        reset_state()
    if key is not None:
        state.last_id = key

    h, w = img.shape[:2]
    shapes, tracked = [], []
    for o in f.objects:
        pts = np.asarray(o.contour, dtype=np.float32).reshape(-1, 2)
        if len(pts) < 3:
            continue
        # 픽셀 좌표로 왔으면 0~1 로 바꾼다. 화면 밖으로 살짝 나간 과자는 0~1 좌표여도 1 을 조금 넘으므로 기준은 2
        if pts.max() > 2.0:
            pts = pts / np.array([f.width or w, f.height or h], dtype=np.float32)
        result = "WAIT" if touches_edge(pts) else o.result
        cx, cy = centroid(pts)
        shapes.append((pts, o.circularity))
        tracked.append(Tracked(cx, cy, result))
    track(tracked)
    items = [(pts, t.result, circ, t.tid) for (pts, circ), t in zip(shapes, tracked)]

    latency_ms = None if f.timestamp is None else int((time.time() - f.timestamp) * 1000)
    defect_in_frame = sum(t.result == "FAIL" for t in tracked)
    summary = {
        "in_frame": len(tracked),
        "defect_in_frame": defect_in_frame,
        "passed_total": state.passed_total,
        "defect_total": state.defect_total,
    }
    hud = [
        f"FRAME {f.frame_id}  {'-' if latency_ms is None else latency_ms} ms",
        f"IN FRAME {len(tracked)}  NG {defect_in_frame}",
        f"PASSED {state.passed_total}  NG {state.defect_total}",
    ]

    t0 = time.perf_counter()
    jpeg = await run_in_threadpool(render_jpeg, img, items, hud)
    render_ms = (time.perf_counter() - t0) * 1000

    # 그리는 사이 더 최신 프레임이 먼저 끝났으면, 이 프레임을 보내면 화면이 뒤로 튄다
    if key is not None:
        if key < state.last_queued:
            return dropped(f)
        state.last_queued = key
    state.latest_jpeg = jpeg

    enqueue({
        "frame_id": f.frame_id,
        "timestamp": f.timestamp,
        "latency_ms": latency_ms,
        "image": base64.b64encode(jpeg).decode(),
        "summary": summary,
        "objects": [
            {"id": tid, "result": result, "circularity": round(circ, 4)}
            for _, result, circ, tid in items
        ],
    })
    metrics.processed += 1
    metrics.recent.append((time.time(), latency_ms, render_ms))
    return {"status": "ok", "frame_id": f.frame_id}


def reset_state() -> None:
    global state
    state = State()


@app.post("/reset")
def reset():
    """누적 집계, frame_id 기록, 측정값을 초기화한다. 시연이나 성능 측정을 새로 시작할 때 호출."""
    global metrics
    reset_state()
    metrics = Metrics()
    return {"status": "ok"}


@app.get("/metrics")
def get_metrics():
    """성능 측정용. fps 와 지연시간은 최근 METRICS_WINDOW 초 기준이다."""
    now = time.time()
    window = min(METRICS_WINDOW, max(now - metrics.started, 1e-6))
    recent = [r for r in metrics.recent if now - r[0] <= window]
    lat = sorted(r[1] for r in recent if r[1] is not None)
    return {
        "window_s": round(window, 1),
        "fps": round(len(recent) / window, 2),
        "latency_ms": {
            "avg": round(sum(lat) / len(lat), 1) if lat else None,
            "p95": lat[int(len(lat) * 0.95)] if lat else None,
            "max": lat[-1] if lat else None,
        },
        "render_ms_avg": round(sum(r[2] for r in recent) / len(recent), 1) if recent else None,
        "counts": {
            "received": metrics.received,
            "processed": metrics.processed,
            "dropped_old": metrics.dropped_old,
            "sent": metrics.sent,
            "send_errors": metrics.send_errors,
            "send_skipped": metrics.send_skipped,
        },
    }


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
