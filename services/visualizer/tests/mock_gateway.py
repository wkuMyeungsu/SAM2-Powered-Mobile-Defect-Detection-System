"""visualizer 단독 테스트용 가짜 gateway (port 8001).

/result 로 받은 마킹 프레임을 tests/out/ 에 저장하고 요약을 출력한다.
실행: python tests/mock_gateway.py
"""
import base64
from pathlib import Path

import uvicorn
from fastapi import FastAPI

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)

app = FastAPI(title="mock gateway")


@app.post("/result")
async def result(payload: dict):
    fid = str(payload["frame_id"])  # 숫자 또는 문자열
    (OUT / f"frame_{fid.zfill(5)}.jpg").write_bytes(base64.b64decode(payload["image"]))
    s = payload["summary"]
    latency = "-" if payload["latency_ms"] is None else payload["latency_ms"]
    print(
        f"frame {fid:>5} | {latency:>4} ms | "
        f"화면 {s['in_frame']}개 (불량 {s['defect_in_frame']}) | "
        f"누적 {s['passed_total']}개 (불량 {s['defect_total']})",
        flush=True,
    )
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="warning")
