"""화면이 이상하게 나올 때 어느 단계 문제인지 가려내는 진단 도구.

휴대폰으로 찍은 프레임 1장을 각 서비스에 차례로 넣고, 단계마다 결과를 이미지로 저장한다.

  1_preprocessing.jpg   preprocessing 이 과자 후보로 잡은 박스 (POST :8002/preprocess)
  2_inference.jpg       inference 가 돌려준 윤곽선을 이 도구가 직접 그린 것 (visualizer 를 거치지 않음)
  3_visualizer.jpg      visualizer 가 그린 것 (GET :8004/latest.jpg)
  compare.jpg           1·2·3 을 나란히 붙인 것

2 와 3 의 윤곽선 위치·모양이 같으면 visualizer 는 받은 대로 그린 것이다.
1 에서 과자에 박스가 없으면 preprocessing, 박스는 맞는데 2 의 윤곽선이 틀리면 inference 문제다.

실행 (docker compose 로 preprocessing·inference·visualizer 가 떠 있을 때, 휴대폰 촬영은 멈춘 상태로):
  pip install httpx opencv-python-headless numpy
  python services/visualizer/tests/diagnose.py 휴대폰프레임.jpg
"""
import argparse
import base64
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

COLORS = {"PASS": (0, 200, 0), "FAIL": (0, 0, 255), "WAIT": (160, 160, 160)}


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def decode(data: bytes) -> np.ndarray:
    # cv2.imread·imwrite 는 Windows 에서 한글 경로를 다루지 못하고 조용히 실패하므로 바이트로 읽고 쓴다
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def save(path: Path, img: np.ndarray) -> None:
    path.write_bytes(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())


def label(img, text, org, color):
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def side_by_side(images: list[tuple[np.ndarray, str]], h: int = 520) -> np.ndarray:
    tiles = []
    for im, title in images:
        im = cv2.resize(im, (int(im.shape[1] * h / im.shape[0]), h))
        cv2.rectangle(im, (0, h - 26), (im.shape[1], h), (0, 0, 0), -1)
        cv2.putText(im, title, (6, h - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        tiles += [im, np.full((h, 8, 3), 255, np.uint8)]
    return np.hstack(tiles[:-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", help="휴대폰으로 찍은 프레임 (JPEG/PNG)")
    ap.add_argument("--pre", default="http://localhost:8002")
    ap.add_argument("--inf", default="http://localhost:8003")
    ap.add_argument("--vis", default="http://localhost:8004")
    ap.add_argument("--out", default=str(Path(__file__).parent / "diagnose_out"))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = Path(args.image).read_bytes()
    orig = decode(raw)
    if orig is None:
        raise SystemExit("이미지를 읽을 수 없습니다")
    oh, ow = orig.shape[:2]
    print(f"입력 이미지 {ow}x{oh}")
    client = httpx.Client(timeout=60)

    # 1. preprocessing: 과자 후보 박스
    ctype = "image/png" if raw[:4] == b"\x89PNG" else "image/jpeg"
    r = client.post(f"{args.pre}/preprocess", content=raw,
                    headers={"Content-Type": ctype, "X-Frame-Id": "diagnose", "X-Timestamp": f"{time.time():.3f}"})
    r.raise_for_status()
    pre = r.json()
    img1 = decode(base64.b64decode(pre["image"]))
    ph, pw = img1.shape[:2]
    cands = pre.get("candidates", [])
    big = 0
    print(f"\n[1] preprocessing: 후보 {len(cands)}개 (전처리 이미지 {pw}x{ph})")
    for i, c in enumerate(cands):
        x1, y1, x2, y2 = (int(v) for v in c["bbox"])
        ratio = (x2 - x1) * (y2 - y1) / (pw * ph)
        aspect = (x2 - x1) / max(1, y2 - y1)
        too_big = ratio > 0.05
        big += too_big
        color = (0, 0, 255) if too_big else (0, 220, 255)
        cv2.rectangle(img1, (x1, y1), (x2, y2), color, 2)
        label(img1, f"{i} {ratio * 100:.1f}%", (x1 + 3, y1 + 16), color)
        print(f"  후보 {i:2d}: 화면의 {ratio * 100:5.1f}%  가로/세로 {aspect:4.2f}" + ("  ← 과자라기엔 너무 큼" if too_big else ""))
    save(out / "1_preprocessing.jpg", img1)

    # 2. inference: 윤곽선 (visualizer 를 거치지 않고 이 도구가 직접 그린다)
    body = {**pre, "frame_id": "diagnose", "timestamp": time.time(), "original_image": b64(raw)}
    r = client.post(f"{args.inf}/infer", json=body)
    if r.status_code == 503:
        raise SystemExit("inference 가 다른 프레임을 처리 중입니다 (503). 휴대폰 촬영을 멈추고 다시 실행하세요.")
    r.raise_for_status()
    inf = r.json()
    objs = inf.get("objects", [])
    img2 = orig.copy()
    print(f"\n[2] inference: 물체 {len(objs)}개, {inf.get('elapsed_ms')}ms, visualizer 전송 {inf.get('visualizer')}")
    for i, o in enumerate(objs):
        pts = np.array(o["contour"], np.float32).reshape(-1, 2)
        px = np.round(pts * [ow, oh]).astype(np.int32)
        color = COLORS.get(o["result"], (255, 0, 255))
        cv2.polylines(img2, [px], True, color, 2)
        x0, y0 = px.min(axis=0)
        label(img2, f"{o['result']} {o['circularity']:.2f}", (int(x0), max(int(y0) - 4, 12)), color)
        print(f"  물체 {i:2d}: {o['result']:4s} 원형도 {o['circularity']:.2f}  화면의 {cv2.contourArea(pts) * 100:5.1f}%  이유 {o.get('reason') or '-'}")
    save(out / "2_inference.jpg", img2)

    # 3. visualizer 결과
    time.sleep(0.5)
    r = client.get(f"{args.vis}/latest.jpg")
    if r.status_code == 200:
        (out / "3_visualizer.jpg").write_bytes(r.content)
        img3 = decode(r.content)
        print(f"\n[3] visualizer: {img3.shape[1]}x{img3.shape[0]} 이미지 저장")
        save(out / "compare.jpg", side_by_side([(img1, "1 preprocessing"), (img2, "2 inference (direct)"), (img3, "3 visualizer")]))
    else:
        print(f"\n[3] visualizer: /latest.jpg 응답 {r.status_code}")
    m = client.get(f"{args.vis}/metrics").json()
    print(f"    visualizer 그리기 평균 {m.get('render_ms_avg')}ms, 지연(gateway 수신→visualizer 완료) 평균 {m['latency_ms']['avg']}ms")

    print("\n[판단 도움]")
    if not cands:
        print("  - preprocessing 이 후보를 하나도 못 찾았습니다 → preprocessing 확인")
    elif big:
        print(f"  - 후보 {len(cands)}개 중 {big}개가 화면의 5%보다 큽니다. 과자가 아닌 배경(모니터·벽·책상)이 후보로 잡혔을 가능성 → preprocessing 확인, 촬영 범위 확인")
    print("  - compare.jpg 에서 2 와 3 의 윤곽선 위치·모양이 같으면 visualizer 는 받은 대로 그린 것입니다")
    print(f"\n결과 이미지: {out.resolve()}")


if __name__ == "__main__":
    main()
