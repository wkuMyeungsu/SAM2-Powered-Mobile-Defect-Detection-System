"""오프라인 보정: 사진 폴더 → SAM 2 → 점수 CSV (+ 외곽선 그린 이미지).

박스는 같은 이름의 라벨 JSON(시뮬레이터 내보내기)이 있으면 그 bbox 를, 없으면 Otsu 로 찾는다.
라벨이 있으면 정답과 비교한 오분류 수도 출력한다.

실행: python tools/calibrate.py data/sim --out data/calib
"""
import argparse
import base64
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402
from segment import Sam2Segmenter  # noqa: E402

COLORS = {"PASS": (0, 200, 0), "FAIL": (0, 0, 255), "WAIT": (160, 160, 160)}


def otsu_candidates(bgr: np.ndarray) -> list[dict]:
    gray = cv2.GaussianBlur(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    _, m = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = 0.002 * m.size
    out = []
    for c in cnts:
        if cv2.contourArea(c) >= min_area:
            x, y, w, h = cv2.boundingRect(c)
            out.append({"bbox": [x, y, x + w, y + h]})
    return out


def label_candidates(path: Path) -> tuple[list[dict], list[dict]]:
    anns = json.loads(path.read_text())["annotations"]
    cands = [{"bbox": [x, y, x + w, y + h]} for x, y, w, h in (a["bbox"] for a in anns)]
    return cands, anns


def main_():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="사진 폴더 (png/jpg, 선택적으로 같은 이름의 .json 라벨)")
    ap.add_argument("--out", default="data/calib")
    ap.add_argument("--pad", type=float, default=3, help="라벨 박스를 이만큼(px) 넓혀 SAM 2 에 넣는다")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    main.segmenter = Sam2Segmenter()
    rows, wrong = [], []

    for img_path in sorted(p for p in Path(args.src).iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")):
        bgr = cv2.imread(str(img_path))
        label = img_path.with_suffix(".json")
        if label.exists():
            cands, anns = label_candidates(label)
            for c in cands:
                c["bbox"] = [c["bbox"][0] - args.pad, c["bbox"][1] - args.pad,
                             c["bbox"][2] + args.pad, c["bbox"][3] + args.pad]
        else:
            cands, anns = otsu_candidates(bgr), None

        _, buf = cv2.imencode(".png", bgr)
        msg = main.judge_frame({"frame_id": img_path.stem, "image": base64.b64encode(buf).decode(),
                                "candidates": cands})
        h, w = bgr.shape[:2]
        for o in msg["objects"]:
            i = o["candidate"]
            gt = anns[i]["defect_type"] or "round" if anns else ""
            row = {"frame": img_path.name, "idx": i, "gt": gt, **{k: o[k] for k in
                   ("circularity", "solidity", "fill", "score", "result", "reason")}}
            rows.append(row)
            if anns and o["result"] != "WAIT" and (o["result"] == "FAIL") != (gt != "round"):
                wrong.append(row)
            pts = (np.array(o["contour"]) * [w, h]).astype(np.int32)
            cv2.polylines(bgr, [pts], True, COLORS[o["result"]], 2)
            cv2.putText(bgr, f"{o['fill']:.2f}", tuple(pts.min(axis=0)), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        COLORS[o["result"]], 1, cv2.LINE_AA)
        cv2.imwrite(str(out / img_path.name), bgr)

    with open(out / "scores.csv", "w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)

    print(f"{len(rows)}개 측정 → {out / 'scores.csv'}")
    if any(r["gt"] for r in rows):
        for gt in sorted({r["gt"] for r in rows}):
            g = [r for r in rows if r["gt"] == gt and r["result"] != "WAIT"]
            if not g:
                continue
            col = lambda k: np.array([r[k] for r in g])  # noqa: E731
            print(f"  {gt:6s} n={len(g):3d}  FAIL {sum(r['result'] == 'FAIL' for r in g):3d}  "
                  f"circ {col('circularity').min():.3f}~{col('circularity').max():.3f}  "
                  f"solid {col('solidity').min():.3f}~{col('solidity').max():.3f}  "
                  f"fill {col('fill').min():.3f}~{col('fill').max():.3f}  "
                  f"score≥{col('score').min():.2f}")
        print(f"  오분류 {len(wrong)}개 (WAIT 제외)")
        for r in wrong:
            print("   ", r)


if __name__ == "__main__":
    main_()
