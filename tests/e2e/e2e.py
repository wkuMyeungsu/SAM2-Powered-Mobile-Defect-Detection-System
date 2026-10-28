"""전체 연결 테스트: 휴대폰 대신 합성 컨베이어 JPEG를 보내고 web 대시보드 자리에서 결과를 받는다.

휴대폰 →① gateway →② preprocessing →③ inference →④ visualizer →⑤ gateway →⑥ /ws/view
실행: docker compose --profile test run --rm --build e2e
"""
import asyncio
import base64
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import websockets

GATEWAY = os.environ.get("GATEWAY", "gateway:8001")
PREPROCESSING = os.environ.get("PREPROCESSING", "preprocessing:8002")
INFERENCE = os.environ.get("INFERENCE", "inference:8003")
VISUALIZER = os.environ.get("VISUALIZER", "visualizer:8004")
WEB = os.environ.get("WEB", "web:8000")
OUT = Path(os.environ.get("OUT_DIR", "/out"))

W, H = 1024, 576
FPS, FRAMES, SPEED = 5, 100, 6  # 과자가 프레임마다 6px씩 왼쪽으로 이동 (CPU 추론 속도에 맞춘 느린 벨트)
SCAN_X = 0.34  # visualizer 검사선 기본값
BELT, COOKIE = (45, 45, 50), (90, 170, 215)  # BGR
R, PITCH, ROWS = 48, 160, (110, 288, 466)
# 열마다 위→아래 3개. 앞 두 열(6개)이 테스트 동안 검사선을 지나며, 그중 2개가 불량
KINDS = ["round", "bite", "round", "round", "round", "cut", "half", "round", "round", "round", "bite", "round"]
cookies = [(W - 300 + (i // 3) * PITCH, ROWS[i % 3], kind) for i, kind in enumerate(KINDS)]


def draw_cookie(img, x, y, kind):
    cv2.circle(img, (x, y), R, COOKIE, cv2.FILLED)
    if kind == "bite":  # 한 입 베어 문 자국
        cv2.circle(img, (x + R, y - R // 3), int(R * 0.55), BELT, cv2.FILLED)
    elif kind == "cut":  # 한쪽이 잘림
        cv2.rectangle(img, (x + R // 3, y - R - 1), (x + R + 1, y + R + 1), BELT, cv2.FILLED)
    elif kind == "half":  # 반쪽
        cv2.rectangle(img, (x - R - 1, y), (x + R + 1, y + R + 1), BELT, cv2.FILLED)


def frame(t):
    img = np.full((H, W, 3), BELT, np.uint8)
    for x0, y, kind in cookies:
        draw_cookie(img, x0 - t * SPEED, y, kind)
    return cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


def expected_counts():
    line = SCAN_X * W
    crossed = [k for x0, _, k in cookies if x0 > line >= x0 - (FRAMES - 1) * SPEED]
    return len(crossed), sum(k != "round" for k in crossed)


def http(url, method="GET"):
    with urllib.request.urlopen(urllib.request.Request(f"http://{url}", method=method), timeout=10) as r:
        body = r.read()
    return json.loads(body) if body[:1] in (b"{", b"[") else body


async def run():
    failures = []

    def check(ok, message):
        print(("  OK   " if ok else "  FAIL ") + message)
        if not ok:
            failures.append(message)

    print("[1] health")
    for name, addr in (("gateway", GATEWAY), ("preprocessing", PREPROCESSING), ("inference", INFERENCE), ("visualizer", VISUALIZER)):
        try:
            check(http(f"{addr}/health").get("status") == "ok", f"{name} /health")
        except Exception as e:
            check(False, f"{name} /health: {e}")
    try:
        check(b"<html" in http(f"{WEB}/").lower(), "web 대시보드 페이지")
    except Exception as e:
        check(False, f"web: {e}")
    if failures:
        return failures

    print(f"\n[2] 휴대폰 흉내: {FRAMES}장을 초당 {FPS}장으로 전송 (약 {FRAMES // FPS}초)")
    http(f"{VISUALIZER}/reset", "POST")  # 누적 집계를 0부터 센다
    sid = http(f"{GATEWAY}/api/default-session")["session_id"]
    results = []

    async def dashboard():
        async with websockets.connect(f"ws://{GATEWAY}/ws/view", max_size=None) as ws:
            async for msg in ws:
                results.append((time.time(), json.loads(msg)))

    dash = asyncio.create_task(dashboard())
    await asyncio.sleep(0.5)
    started = time.time()
    async with websockets.connect(f"ws://{GATEWAY}/ws/phone/{sid}", max_size=None) as phone:
        config = json.loads(await phone.recv())
        check(config.get("type") == "config", f"gateway 촬영 설정 수신 {config}")
        await phone.send(json.dumps({"type": "hello", "width": W, "height": H, "user_agent": "e2e"}))
        for t in range(FRAMES):
            await phone.send(frame(t))
            await asyncio.sleep(max(0, started + (t + 1) / FPS - time.time()))
        await asyncio.sleep(8)  # 마지막 프레임 추론을 기다린다
    dash.cancel()

    print("\n[3] 결과")
    res = [r for ts, r in results if ts >= started and r.get("frame_id") is not None]
    check(len(res) > 0, f"대시보드가 받은 결과 {len(res)}건")
    if not res:
        return failures
    ids = [r["frame_id"] for r in res]
    check(ids == sorted(ids) and len(ids) == len(set(ids)), "frame_id 오름차순, 중복 없음")
    check(all(isinstance(r.get("image"), str) and r["image"] for r in res), "모든 결과에 마킹 이미지 포함")
    verdicts = {o["result"] for r in res for o in r["objects"]}
    check({"PASS", "FAIL"} <= verdicts, f"PASS·FAIL 판정 모두 나옴 {sorted(verdicts)}")

    session = http(f"{GATEWAY}/api/sessions/{sid}")
    pre = http(f"{PREPROCESSING}/health")["forward"]
    metrics = http(f"{VISUALIZER}/metrics")
    check(session["forward_errors"] == 0, f"gateway 전달 오류 {session['forward_errors']} ({session['last_error']})")
    check(pre["errors"] == 0, f"preprocessing → inference 오류 {pre['errors']}")
    check(metrics["counts"]["send_errors"] == 0, f"visualizer → gateway 오류 {metrics['counts']['send_errors']}")

    summary = res[-1]["summary"]
    total, defects = expected_counts()
    counted = summary["passed_total"] == total and summary["defect_total"] == defects
    print(f"  {'OK  ' if counted else 'WARN'} 누적 집계 통과 {summary['passed_total']}/{total}, 불량 {summary['defect_total']}/{defects}"
          + ("" if counted else "  (추론이 느려 프레임 사이 이동이 MAX_JUMP를 넘으면 적게 센다)"))

    lat = [r["latency_ms"] for r in res if r.get("latency_ms") is not None]
    span = results[-1][0] - started
    print(f"\n[4] 성능: 결과 {len(res)}건 / 보낸 {FRAMES}장, 약 {len(res) / span:.2f} fps"
          + (f", 지연 중앙값 {statistics.median(lat):.0f}ms (최대 {max(lat):.0f}ms)" if lat else ""))

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "input.jpg").write_bytes(frame(FRAMES // 2))
    (OUT / "marked.jpg").write_bytes(base64.b64decode(res[-1]["image"].split(",")[-1]))
    print(f"  입력·마킹 이미지 저장: tests/e2e/out/")
    return failures


failures = asyncio.run(run())
print("\n" + ("통과" if not failures else f"실패 {len(failures)}건: " + "; ".join(failures)))
sys.exit(1 if failures else 0)
