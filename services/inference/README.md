# inference (C3) — SAM 2 추론·판정

preprocessing이 찾은 과자 박스를 SAM 2(`sam2.1-hiera-tiny`) 프롬프트로 넣어 정확한 외곽선을 얻고, 형상을 재서 `PASS`/`FAIL`을 판정한 뒤 visualizer로 보낸다.

## 처리 순서

1. 이미지 디코드 → RGB 변환
2. SAM 2: 프레임당 `set_image` 1회, 박스 N개를 한 번에 `predict`
3. 마스크 정리 — 박스 밖 제거 → 가장 큰 덩어리만 → 닫힘 연산 → 구멍(초코칩) 메움
4. 측정 — 원형도, 볼록도, 원 채움률
5. 판정 — 아래 기준 중 하나라도 미달이면 `FAIL`. 화면 테두리에 닿은 박스, SAM 2 점수가 낮은 마스크는 `WAIT`(판정 보류)
6. visualizer로 `POST` (2초 제한). 실패해도 로그만 남기고 계속 처리

## 판정 기준

| 값 | 식 | 잡는 불량 | 기준 (환경 변수) |
|---|---|---|---|
| 볼록도 solidity | 면적 / 볼록 껍질 면적 | 한쪽 파임(bite), 조각 결손(wedge) | `SOLID_MIN` 0.975 |
| 원 채움률 fill | 면적 / 외접원 면적 | 단면 절단(cut), 반쪽 파손(half) | `FILL_MIN` 0.95 |
| 원형도 circularity | 4π·면적 / 둘레² | 보조 안전장치 | `CIRC_MIN` 0.80 |

**판정 이유(`reason`)** 는 어느 기준이 먼저 걸렸는지가 아니라 **패인 깊이(notch)** 로 고른다. 파임도 원 채움률을 떨어뜨리고 크게 잘린 면도 볼록도를 조금 떨어뜨려서, 기준 순서로는 유형을 구분할 수 없기 때문이다.

| notch (가장 깊이 패인 곳 / 외접원 반지름) | reason |
|---|---|
| `NOTCH_MIN` 0.08 이상 (파임 0.10~0.34, 조각 결손 0.60~0.84) | `notch (bite or missing piece)` |
| 미만 (단면 절단·반쪽 파손 0.06 이하, 정상 0.02 안팎) | `straight cut or broken` |
| 볼록도·채움률은 통과, 원형도만 미달 | `low circularity` |

**원형도만으로는 부족하다.** 픽셀 계단 때문에 정상 과자도 0.87~0.90이 나오고, 단면 절단은 최대 0.88까지 올라가 두 그룹이 겹친다. 단면 절단·반쪽 파손은 볼록한 모양이라 볼록도로도 못 잡는다. 그래서 외접원 대비 면적(fill)을 추가했다.

시뮬레이터(`conveyor-cookie-sim.html`, 기본 설정: 불량 강도 55, 지름 78px) 실측 결과:

| 정답 | 개수 | FAIL | 원형도 | 볼록도 | 원 채움률 |
|---|---|---|---|---|---|
| 정상 round | 181 | 0 | 0.880~0.904 | 0.986~0.990 | 0.958~0.975 |
| 한쪽 파임 bite | 18 | 18 | 0.814~0.844 | **0.946~0.961** | 0.901~0.926 |
| 단면 절단 cut | 13 | 13 | 0.831~0.859 | 0.984~0.988 | **0.826~0.840** |
| 조각 결손 wedge | 24 | 24 | 0.591~0.619 | **0.857~0.884** | 0.814~0.855 |
| 반쪽 파손 half | 8 | 8 | 0.701~0.744 | 0.972~0.983 | **0.543~0.560** |

SAM 2 실제 마스크, 20프레임 292개 중 테두리에 걸친 48개(`WAIT`) 제외 244개, 오분류 0. 판정 이유도 63개 불량 모두 유형과 맞았다. 진아 님 preprocessing 출력(CLAHE 이미지 + Otsu 박스)을 그대로 넣어도 99개 중 오분류 0.

한계: 불량 강도를 20 이하로 내리고 과자를 크게(지름 150px 이상) 찍으면 아주 얕은 파임 일부를 놓친다. **실제 과자·조명에서는 `tools/calibrate.py`로 다시 재서 기준을 바꾼다.**

## API (초안 — `docs/contracts/`에 합의 후 옮길 예정)

### `GET /health`
`{"service": "inference", "status": "ok"}`

### `POST /infer`

preprocessing 응답 JSON을 **그대로** 넣으면 된다. 쓰는 필드:

```json
{
  "frame_id": "123",
  "timestamp": 1761600000.123,
  "image": "<base64 JPEG/PNG>",
  "original_image": "<선택: 원본 프레임 base64. 있으면 visualizer에 이것을 넘김>",
  "candidates": [{"bbox": [214, 182, 427, 395]}]
}
```

- `bbox`는 `image` 기준 픽셀 좌표 `[x1, y1, x2, y2]`.
- `candidates`가 없거나 비어 있으면 `objects: []`.

응답 `200` (visualizer로 보낸 것과 같은 `objects`):

```json
{
  "frame_id": "123",
  "objects": [
    {
      "contour": [[0.312, 0.420], [0.330, 0.401], [0.351, 0.398]],
      "candidate": 0,
      "bbox": [214.0, 182.0, 427.0, 395.0],
      "circularity": 0.8312, "solidity": 0.9871, "fill": 0.8344, "notch": 0.021, "score": 0.98,
      "result": "FAIL", "reason": "straight cut or broken"
    }
  ],
  "elapsed_ms": 850.2,
  "visualizer": "ok"
}
```

- `contour`는 **0~1 정규화 좌표** (x ÷ 너비, y ÷ 높이). `approxPolyDP`(1px)로 점을 줄였다.
- `candidate`는 요청 `candidates`의 순번.
- `result`: `PASS` / `FAIL` / `WAIT`. `reason`은 판정 근거.
- `visualizer`: `ok` / `dropped` / `visualizer_error` / `disabled`.

오류: `400` JSON·이미지·bbox 형식 오류, `503` 이전 프레임 처리 중 (기다리지 말고 이 프레임을 버릴 것). 본문은 `{"error": "..."}`.

### visualizer로 보내는 메시지

`POST $VISUALIZER_URL` — 도은 님 `contracts/inference-visualizer.md` 형식:
`frame_id`, `timestamp`(있으면), `image`, `objects[]`. visualizer가 모르는 필드(`solidity`, `fill`, `reason` 등)는 무시된다.

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `VISUALIZER_URL` | `http://visualizer:8004/visualize` | 비우면 전송하지 않음 |
| `VISUALIZER_TIMEOUT` | 2 | 초 |
| `SOLID_MIN` / `FILL_MIN` / `CIRC_MIN` | 0.975 / 0.95 / 0.80 | 판정 기준 |
| `NOTCH_MIN` | 0.08 | 판정 이유 구분용 (판정 자체에는 영향 없음) |
| `SCORE_MIN` | 0.80 | 이보다 낮은 SAM 2 점수는 `WAIT` |
| `MIN_MASK_AREA` | 200 | 이보다 작은 마스크(px)는 버림 |
| `SAM2_MODEL` | `facebook/sam2.1-hiera-tiny` | 빌드 인자로도 바꿀 수 있음 |
| `DEVICE` | `auto` | `cuda` 있으면 cuda, 아니면 cpu |

기준값은 `docker-compose.yml`의 `environment`나 `.env`로 바꾸면 다시 빌드할 필요가 없다.

## 실행·확인

```bash
docker compose up --build inference      # 첫 빌드는 torch·가중치 받느라 몇 분 걸림 (이미지 약 1.6GB)
docker compose run --rm --no-deps inference python -m unittest -v test_inference
```

- 가중치는 빌드할 때 이미지 안에 받아 둔다. 코드만 바꿔 다시 빌드하면 캐시를 쓰므로 다시 받지 않는다.
- CPU 기준 과자 15개 프레임 1장에 약 1~2초. 처리 중 들어온 프레임은 `503`으로 바로 거절해서 결과가 밀리지 않는다.

## 기준값 다시 재기 (로컬)

```bash
cd services/inference
python -m venv .venv && source .venv/bin/activate
pip install torch torchvision && SAM2_BUILD_CUDA=0 pip install --no-build-isolation -r requirements.txt -r requirements-dev.txt
python -m playwright install chromium

python tools/capture_sim.py --html <경로>/conveyor-cookie-sim.html --frames 20   # → data/sim/
python tools/calibrate.py data/sim --out data/calib                               # → scores.csv, 외곽선 그린 이미지, 오분류 목록
```

`calibrate.py`는 같은 이름의 `.json` 라벨이 있으면 정답과 비교하고, 없으면(실제 사진) Otsu로 박스를 찾아 점수만 낸다. `data/`는 커밋하지 않는다.
