"""C3 Inference (port 8003)

preprocessing 이 찾은 과자 박스를 SAM 2 프롬프트로 써서 정확한 외곽선을 얻고,
형상을 재서 PASS/FAIL 을 판정한 뒤 visualizer 로 보낸다.
입력은 preprocessing 응답 JSON, 출력은 visualizer contracts/inference-visualizer.md 형식.
"""
import asyncio
import base64
import binascii
import json
import logging
import os
import time
from pathlib import Path
from contextlib import asynccontextmanager

import cv2
import httpx
import numpy as np
import uvicorn
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

import judge

SERVICE = os.environ.get("SERVICE_NAME", "inference")
PORT = int(os.environ.get("PORT", "8003"))
VISUALIZER_URL = os.environ.get("VISUALIZER_URL", "http://visualizer:8004/visualize")  # 비우면 전송하지 않음
VISUALIZER_TIMEOUT = float(os.environ.get("VISUALIZER_TIMEOUT", "2"))
# 정하면 받은 프레임과 판정 결과를 이 폴더에 남긴다 (현장 보정·디버그용). 최근 DUMP_KEEP 프레임만 둔다.
DUMP_DIR = os.environ.get("DUMP_DIR", "")
DUMP_KEEP = int(os.environ.get("DUMP_KEEP", "300"))
TH = judge.Thresholds()

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("inference")
log.setLevel(logging.INFO)  # SAM 2 가 루트 로거에 프레임마다 INFO 를 찍어서 우리 로거만 INFO 로 둔다

segmenter = None  # lifespan 에서 SAM 2 를 한 번 읽는다. 테스트에서는 가짜로 바꿔 끼운다.
busy = asyncio.Lock()


class BadRequest(ValueError):
    pass


def decode_image(b64: str) -> np.ndarray:
    if not isinstance(b64, str) or not b64:
        raise BadRequest("image 가 없습니다")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        buf = np.frombuffer(base64.b64decode(b64, validate=True), np.uint8)
    except (binascii.Error, ValueError):
        raise BadRequest("image 가 올바른 base64 가 아닙니다")
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise BadRequest("image 디코딩 실패 (JPEG/PNG 만 지원)")
    return img


def parse_boxes(candidates, w: int, h: int) -> list[tuple[int, list[float]]]:
    """(candidates 순번, 이미지 안으로 자른 박스) 목록. 넓이가 0 인 박스는 버린다."""
    boxes = []
    for i, c in enumerate(candidates or []):
        try:
            x1, y1, x2, y2 = (float(v) for v in c["bbox"])
        except (KeyError, TypeError, ValueError):
            raise BadRequest("candidates[].bbox 는 [x1, y1, x2, y2] 여야 합니다")
        x1, x2 = max(0.0, x1), min(w - 1.0, x2)
        y1, y2 = max(0.0, y1), min(h - 1.0, y2)
        if x2 > x1 and y2 > y1:
            boxes.append((i, [x1, y1, x2, y2]))
    return boxes


def judge_frame(req: dict) -> dict:
    """프레임 하나 → visualizer 로 보낼 메시지. 단계 2~5 (분할·정리·측정·판정)."""
    bgr = decode_image(req.get("image"))
    h, w = bgr.shape[:2]
    boxes, empty = judge.cookie_boxes(bgr, parse_boxes(req.get("candidates"), w, h), TH)
    masks, scores = segmenter.segment(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), [b for _, b in boxes])

    objects = []
    dropped = [{"candidate": idx, "why": "no cookie color in box"} for idx in empty]
    for (idx, box), mask, score in zip(boxes, masks, scores):
        mask = judge.clean_mask(mask, box)
        m = judge.measure(mask)
        if m is None or m["area"] < TH.min_area:
            dropped.append({"candidate": idx, "why": "small mask", "area": m["area"] if m else 0})
            continue
        hue, sat = judge.mask_color(bgr, mask)
        if not judge.is_cookie_color(hue, sat, TH):
            # 화면 글자·벨트 무늬 같은 쿠키가 아닌 후보
            dropped.append({"candidate": idx, "why": "not cookie color", "hue": hue, "sat": sat})
            continue
        if judge.touches_border(box, w, h):
            # 잘린 쪽이 직선이라 항상 불량처럼 보인다. 판정은 보류한다.
            result, reason = "WAIT", "touches frame edge"
        elif score < TH.score_min:
            result, reason = "WAIT", "low mask score"
        else:
            result, reason = judge.decide(m, TH)
        contour = judge.simplify(m["contour"]) / [w, h]
        objects.append({
            # 0~1 정규화 좌표라 visualizer 가 원본이든 축소본이든 맞춰 그린다.
            "contour": contour.round(4).tolist(),
            "candidate": idx,
            "bbox": [round(v, 1) for v in box],
            "circularity": round(m["circularity"], 4),
            "solidity": round(m["solidity"], 4),
            "fill": round(m["fill"], 4),
            "notch": round(m["notch"], 4),
            "straight": round(m["straight"], 4),
            "score": round(float(score), 4),
            "result": result,
            "reason": reason,
        })

    out = {
        "frame_id": req.get("frame_id"),
        # 원본 프레임이 함께 오면 그것을, 아니면 전처리된 프레임을 넘긴다.
        "image": req.get("original_image") or req["image"],
        "objects": objects,
        "dropped": dropped,  # visualizer 로는 보내지 않는다
    }
    if req.get("timestamp") is not None:
        out["timestamp"] = req["timestamp"]
    return out


def dump(req: dict, msg: dict, dropped: list) -> None:
    """받은 프레임(JPEG)과 후보·판정·버린 이유(JSON)를 DUMP_DIR 에 남긴다."""
    folder = Path(DUMP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.time():.3f}_{msg['frame_id']}"
    try:
        (folder / f"{name}.jpg").write_bytes(base64.b64decode(req["image"].split(",")[-1]))
        meta = {k: v for k, v in req.items() if k not in ("image", "original_image")}
        (folder / f"{name}.json").write_text(json.dumps(
            {"request": meta, "objects": msg["objects"], "dropped": dropped}, ensure_ascii=False, indent=1))
        for old in sorted(folder.glob("*.jpg"))[:-DUMP_KEEP]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
    except OSError as e:
        log.warning("dump 실패: %s", e)


async def send_to_visualizer(client: httpx.AsyncClient, msg: dict) -> str:
    if not VISUALIZER_URL:
        return "disabled"
    try:
        r = await client.post(VISUALIZER_URL, json=msg, timeout=VISUALIZER_TIMEOUT)
        r.raise_for_status()
        return r.json().get("status", "ok")
    except (httpx.HTTPError, ValueError) as e:
        # visualizer 가 꺼져 있어도 추론은 계속한다.
        log.warning("visualizer 전송 실패 (frame %s): %s", msg.get("frame_id"), e)
        return "visualizer_error"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global segmenter
    if segmenter is None:
        from segment import Sam2Segmenter

        start = time.perf_counter()
        segmenter = Sam2Segmenter()
        log.info("SAM 2 로딩 완료: %s on %s (%.1fs)", segmenter.predictor.model.__class__.__name__,
                 segmenter.device, time.perf_counter() - start)
    async with httpx.AsyncClient() as client:
        app.state.client = client
        yield


app = FastAPI(title="inference", lifespan=lifespan)


@app.get("/health")
def health():
    return {"service": SERVICE, "status": "ok"}


@app.post("/infer")
async def infer(request: Request):
    # 처리 중이면 기다리지 않고 바로 거절한다. preprocessing 은 이 프레임을 버리고 다음 프레임을 보낸다.
    if busy.locked():
        return JSONResponse({"error": "busy"}, status_code=503)
    async with busy:
        start = time.perf_counter()
        try:
            req = await request.json()
            if not isinstance(req, dict):
                raise BadRequest("본문은 JSON 객체여야 합니다")
            msg = await run_in_threadpool(judge_frame, req)
        except ValueError as e:  # BadRequest, JSON 파싱 실패
            return JSONResponse({"error": str(e) or "JSON 본문이 올바르지 않습니다"}, status_code=400)
        elapsed = round((time.perf_counter() - start) * 1000, 1)

    dropped = msg.pop("dropped")
    if DUMP_DIR:
        await run_in_threadpool(dump, req, msg, dropped)
    status = await send_to_visualizer(request.app.state.client, msg)
    log.info("frame %s: 후보 %d → %d개 (FAIL %d, 버림 %d) %.0fms → %s", msg["frame_id"],
             len(req.get("candidates") or []), len(msg["objects"]),
             sum(o["result"] == "FAIL" for o in msg["objects"]), len(dropped), elapsed, status)
    return {
        "frame_id": msg["frame_id"],
        "objects": msg["objects"],
        "dropped": dropped,
        "elapsed_ms": elapsed,
        "visualizer": status,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
