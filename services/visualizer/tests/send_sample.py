"""inference 대신 visualizer 로 테스트 데이터를 보낸다.

1) 합성 모드 (기본): 컨베이어 위를 왼쪽으로 지나가는 과자 프레임을 직접 만들어 보낸다.
   python tests/send_sample.py
   python tests/send_sample.py --shuffle      # 순서가 섞여 도착하는 상황 (inference 여러 대)

2) 시뮬레이터 모드: conveyor-cookie-sim.html 에서 일시정지 후 저장한 PNG + 라벨 JSON 을 보낸다.
   python tests/send_sample.py --image frame.png --labels labels.json
"""
import argparse
import base64
import json
import math
import random
import time
from pathlib import Path

import cv2
import httpx
import numpy as np

THRESHOLD = 0.80  # 데모용 판정 기준. 실제 기준은 inference 담당이 정한다.
SCAN_X = 0.34


def circularity(contour: np.ndarray) -> float:
    area = cv2.contourArea(contour)
    peri = cv2.arcLength(contour, True)
    return 0.0 if peri == 0 else 4 * math.pi * area / peri ** 2


def b64_jpeg(img: np.ndarray) -> str:
    return base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1]).decode()


def synthetic_frames(n: int, w: int = 960, h: int = 450, speed: int = 28):
    rng = random.Random(7)
    lines = [75, 225, 375]
    r, pitch = 39, 168
    cookies = []
    for li, y in enumerate(lines):
        offset = rng.randint(0, pitch)
        for k in range(12):
            cookies.append({
                "line": li, "y": y, "x0": w + r + offset + k * pitch,
                "defect": rng.random() < 0.3, "angle": rng.uniform(0, 2 * math.pi),
            })

    prev_cx, expected = {}, [0, 0]
    for t in range(n):
        img = np.full((h, w, 3), (27, 24, 20), np.uint8)
        for y in lines:
            cv2.rectangle(img, (0, y - 67), (w, y + 67), (49, 46, 42), -1)
        objects = []
        for i, c in enumerate(cookies):
            x = c["x0"] - t * speed
            if x + r < 0 or x - r > w:
                continue
            mask = np.zeros((h, w), np.uint8)
            cv2.circle(mask, (int(x), c["y"]), r, 255, -1)
            if c["defect"]:
                bx, by = x + 1.05 * r * math.cos(c["angle"]), c["y"] + 1.05 * r * math.sin(c["angle"])
                cv2.circle(mask, (int(bx), int(by)), int(r * 0.6), 0, -1)
            img[mask > 0] = (75, 138, 201)
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours or cv2.contourArea(max(contours, key=cv2.contourArea)) < 20:
                continue  # 화면 끝에 몇 픽셀만 걸친 과자
            cnt = max(contours, key=cv2.contourArea)
            circ = circularity(cnt)
            pts = cnt.reshape(-1, 2).astype(float) / [w, h]
            objects.append({
                "contour": pts.round(4).tolist(),
                "circularity": round(circ, 4),
                "result": "FAIL" if circ < THRESHOLD else "PASS",
            })
            # 정답 집계: 테두리에 닿지 않은 상태로 검사선을 넘은 과자
            m = cv2.moments(cnt)
            cx = m["m10"] / m["m00"] / w
            edge = pts.min() <= 0.005 or pts[:, 0].max() >= 0.995
            if i in prev_cx and prev_cx[i] >= SCAN_X > cx and not edge:
                expected[0] += 1
                expected[1] += objects[-1]["result"] == "FAIL"
            prev_cx[i] = cx
        yield img, objects
    print(f"\n[정답] 검사선 통과 {expected[0]}개, 불량 {expected[1]}개 (--shuffle 이면 버려진 프레임만큼 적게 나올 수 있음)")


def simulator_frames(pairs: list[tuple[Path, Path]], verbose: bool):
    first = last = None
    for image_path, labels_path in pairs:
        img = cv2.imread(str(image_path))
        doc = json.loads(labels_path.read_text(encoding="utf-8"))
        w, h = doc["image"]["width"], doc["image"]["height"]
        objects = []
        for ann in doc["annotations"]:
            pts = np.array(ann["segmentation"][0], dtype=np.float32).reshape(-1, 2)
            circ = circularity(pts.reshape(-1, 1, 2))
            objects.append({
                "contour": (pts / [w, h]).round(4).tolist(),
                "circularity": round(circ, 4),
                "result": "FAIL" if ann["category"] == "deformed" else "PASS",  # 라벨 정답을 그대로 사용
            })
            if verbose:
                print(f"id {ann['id']:3d} line {ann['line']} {ann['category']:8s} 원형도 {circ:.3f}")
        first = first or doc["counts"]
        last = doc["counts"]
        yield img, objects
    if len(pairs) > 1:
        # 시뮬레이터 누적 수의 증가분 = 첫 프레임과 마지막 프레임 사이에 검사선을 지난 과자
        print(f"\n[정답] 검사선 통과 {last['total_passed'] - first['total_passed']}개, "
              f"불량 {last['defect_passed'] - first['defect_passed']}개 (시뮬레이터 집계)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8004/visualize")
    ap.add_argument("--frames", type=int, default=60)
    ap.add_argument("--fps", type=float, default=5)
    ap.add_argument("--shuffle", action="store_true", help="이웃한 프레임 순서를 섞어서 보낸다")
    ap.add_argument("--image")
    ap.add_argument("--labels")
    ap.add_argument("--sim-dir", help="capture_sim.py 로 저장한 폴더 (0000.png + 0000.json ...)")
    args = ap.parse_args()

    if args.sim_dir:
        pngs = sorted(Path(args.sim_dir).glob("*.png"))
        source = simulator_frames([(p, p.with_suffix(".json")) for p in pngs], verbose=False)
    elif args.image:
        source = simulator_frames([(Path(args.image), Path(args.labels))], verbose=True)
    else:
        source = synthetic_frames(args.frames)
    batch = []
    with httpx.Client(timeout=5) as client:
        def flush():
            if args.shuffle:
                random.shuffle(batch)
            for p in batch:
                r = client.post(args.url, json=p)
                print(f"send frame {p['frame_id']:3d} -> {r.status_code} {r.json().get('status')}")
                time.sleep(1 / args.fps)
            batch.clear()

        for fid, (img, objects) in enumerate(source):
            batch.append({"frame_id": fid, "timestamp": time.time(), "image": b64_jpeg(img), "objects": objects})
            if len(batch) == 3:
                flush()
        flush()


if __name__ == "__main__":
    main()
