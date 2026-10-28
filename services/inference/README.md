# inference (C3) — SAM 2 추론·판정

preprocessing이 찾은 과자 박스를 SAM 2(`sam2.1-hiera-tiny`) 프롬프트로 넣어 정확한 외곽선을 얻고, 형상을 재서 `PASS`/`FAIL`을 판정한 뒤 visualizer로 보낸다.

## 처리 순서

1. 이미지 디코드 → RGB 변환
2. 박스 다시 잡기 — 화면 전체의 쿠키 색 덩어리를 찾아, preprocessing 박스와 겹치는 덩어리마다 박스 하나를 만든다
   - 쿠키 색이 없는 박스(벨트 무늬·화면 글자)는 SAM 2에 넣지 않는다 (박스당 디코더 0.1~0.2초 절약)
   - 쿠키가 배경과 한 박스로 묶여 와도 쿠키마다 따로 판정한다. 시연에서 preprocessing이 화면 전체를 박스 1개로 보낸 프레임이 많았는데, 예전에는 이런 프레임이 0개로 끝났다
   - 한 쿠키에 박스가 여러 개 와도 한 번만 판정한다
3. SAM 2: 프레임당 `set_image` 1회, 박스 N개를 한 번에 `predict`
4. 마스크 정리 — 박스 밖 제거 → 가장 큰 덩어리만 → 닫힘 연산 → 구멍(초코칩) 메움
5. 마스크 정리 후 다시 한 번, 쿠키 색이 아닌 마스크(화면 글자, 벨트 무늬 등)는 결과에서 뺀다
6. 측정 — 패인 깊이, 가장 긴 직선 변, 원 채움률, 볼록도, 원형도
7. 판정 — 아래 기준 중 하나라도 걸리면 `FAIL`. 화면 테두리에 닿은 박스, SAM 2 점수가 낮은 마스크는 `WAIT`(판정 보류)
8. visualizer로 `POST` (2초 제한). 실패해도 로그만 남기고 계속 처리

## 판정 기준

카메라로 화면이나 실제 벨트를 찍으면 **원근 때문에 원이 타원이 되고, 그림자와 흐림이 섞인다.** 그래서 "원에서 얼마나 벗어났나"(원 채움률, 원형도)가 아니라, 타원이 되어도 변하지 않는 두 특징으로 판정한다.

| 값 | 식 | 잡는 불량 | 기준 (환경 변수) |
|---|---|---|---|
| 패인 깊이 notch | 가장 깊이 패인 곳 / 외접원 반지름 | 한쪽 파임, 조각 결손 | `NOTCH_MIN` 0.08 이상 → `notch (bite or missing piece)` |
| 직선 변 straight | 가장 긴 직선 변 / 외접원 반지름 | 단면 절단, 반쪽 파손 | `STRAIGHT_MIN` 0.85 이상 → `straight cut or broken` |
| 원 채움률 fill | 면적 / 외접원 면적 | 크게 부서진 경우 (안전장치) | `FILL_MIN` 0.75 미만 → `straight cut or broken` |
| 볼록도 solidity | 면적 / 볼록 껍질 면적 | 안전장치 | `SOLID_MIN` 0.95 미만 → `dented outline` |
| 원형도 circularity | 4π·면적 / 둘레² | 안전장치 | `CIRC_MIN` 0.70 미만 → `low circularity` |

- 직선 변은 외곽선을 반지름 2% 오차로 다각형 근사한 뒤, 8° 미만으로 꺾이는 이웃 변을 합친 가장 긴 변이다. 원·타원 둘레에서는 0.67을 넘지 않고, 잘린 면은 0.9~2.0이 나온다.
- **처음 기준(원 채움률 0.95, 볼록도 0.975)은 카메라에서 쓸 수 없었다.** 쿠키가 8%만 납작하게 찍혀도 정상품 채움률이 0.92~0.94로 떨어져 정상품이 전부 FAIL이 됐다(시연 결과).
- 쿠키 색: 마스크 안 HSV 중앙값이 색상 `COOKIE_HUE_MIN`~`COOKIE_HUE_MAX`(5~30), 채도 `COOKIE_SAT_MIN`(60) 이상이어야 쿠키로 본다. 시뮬레이터와 카메라 모두 쿠키는 H 14~16, S 107 이상이고, 화면 글자는 H 96~110, S 20 이하였다. 실제 과자 색이 다르면 바꾸고, `COOKIE_SAT_MIN=0`이면 끈다.

### 검증

시뮬레이터 형상(`sim_shapes.py`)을 15%까지 납작하게 하고 흐리게 한 조건(지름 50/78/120px, 불량 강도 10~100, 조건마다 80개):

- 정상품: 모든 조건에서 오판 0 (예전 기준은 8%만 납작해져도 정상품 80개가 모두 FAIL)
- 불량품: 기본 강도 55 이상은 거의 다 잡는다. 놓치는 것은 강도 10~20의 아주 얕은 파임·절단이 대부분이다 (조건당 최대 10/80).

SAM 2 실제 마스크:

| 데이터 | 결과 |
|---|---|
| 시뮬레이터 20프레임 (정상 181, 불량 63, 테두리 `WAIT` 제외) | 오분류 0, 판정 이유도 모두 유형과 일치 |
| 시연 카메라 프레임 (휴대폰으로 모니터 촬영) | 정상 5개 모두 PASS, 파임·반쪽 파손 FAIL, 화면 글자 후보는 제외 |

한계: **실제 과자·조명에서는 `tools/calibrate.py`로 다시 재서 기준을 바꾼다.**

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
      "circularity": 0.8312, "solidity": 0.9871, "fill": 0.8344, "notch": 0.021, "straight": 1.52, "score": 0.98,
      "result": "FAIL", "reason": "straight cut or broken"
    }
  ],
  "elapsed_ms": 850.2,
  "visualizer": "ok"
}
```

- `contour`는 **0~1 정규화 좌표** (x ÷ 너비, y ÷ 높이). `approxPolyDP`(1px)로 점을 줄였다.
- `candidate`는 그 쿠키를 찾은 요청 `candidates`의 순번. 박스 하나에 쿠키가 여럿이면 같은 순번이 여러 번 나온다.
- 응답의 `dropped`에는 버린 후보와 이유(`no cookie color in box`, `small mask`, `not cookie color`)가 들어간다. visualizer로는 보내지 않는다.
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
| `NOTCH_MIN` / `STRAIGHT_MIN` | 0.08 / 0.85 | 주 판정 기준 |
| `FILL_MIN` / `SOLID_MIN` / `CIRC_MIN` | 0.75 / 0.95 / 0.70 | 안전장치 기준 |
| `COOKIE_HUE_MIN` / `COOKIE_HUE_MAX` / `COOKIE_SAT_MIN` | 5 / 30 / 60 | 쿠키 색 (OpenCV HSV). 채도 0이면 색 검사 끔 |
| `SCORE_MIN` | 0.80 | 이보다 낮은 SAM 2 점수는 `WAIT` |
| `MIN_MASK_AREA` | 200 | 이보다 작은 마스크(px)는 버림 |
| `COOKIE_BLOB_MIN` / `COOKIE_BLOB_MAX` | 0.002 / 0.15 | 쿠키 색 덩어리 크기 (화면 면적 대비). 범위 밖이면 쿠키가 아닌 것으로 봄 |
| `DUMP_DIR` | (없음) | 정하면 받은 프레임(JPEG)과 후보·판정·버린 이유(JSON)를 남김. 현장 보정·디버그용 |
| `DUMP_KEEP` | 300 | `DUMP_DIR`에 남길 최근 프레임 수 |
| `SAM2_MODEL` | `facebook/sam2.1-hiera-tiny` | 빌드 인자로도 바꿀 수 있음 |
| `DEVICE` | `auto` | `cuda` 있으면 cuda, 아니면 cpu |

기준값은 `docker-compose.yml`의 `environment`나 `.env`로 바꾸면 다시 빌드할 필요가 없다.

## 실행·확인

```bash
docker compose up --build inference      # 첫 빌드는 torch·가중치 받느라 몇 분 걸림 (이미지 약 1.6GB)
docker compose run --rm --no-deps inference python -m unittest -v test_inference
```

- 가중치는 빌드할 때 이미지 안에 받아 둔다. 코드만 바꿔 다시 빌드하면 캐시를 쓰므로 다시 받지 않는다.
- CPU 기준 과자 15개 프레임 1장에 약 2초 (Mac Docker, 다른 프로그램이 한가할 때). Zoom·60fps 시뮬레이터 같은 프로그램이 CPU를 쓰면 5초 이상으로 늘어난다. 시연 중에는 다른 프로그램을 닫는다. 처리 중 들어온 프레임은 `503`으로 바로 거절해서 결과가 밀리지 않는다.

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

## 현장 프레임 남기기

루트 `.env`(커밋 안 됨)에 `DUMP_DIR=/app/dump`를 넣고 `docker compose up -d inference`로 다시 띄운 뒤 시연한다.

```bash
docker compose cp inference:/app/dump ./services/inference/data/dump   # 꺼내기 (data/ 는 커밋 안 됨)
```

각 프레임의 `.json`에 후보 박스, 쿠키별 측정값·판정, 버린 이유가 있다.
