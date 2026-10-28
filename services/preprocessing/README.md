# preprocessing (C2) — 전처리

gateway에서 받은 프레임을 SAM 2 추론에 맞게 다듬고, 과자 후보 영역을 찾아 inference에 넘길 프롬프트(박스·점)로 만들어 inference로 보낸다.

```
휴대폰 → gateway(8001) → [preprocessing(8002)] → inference(8003) → visualizer(8004) → gateway → web
```

## 처리 순서

1. 디코드 (JPEG/PNG)
2. 리사이즈 — 긴 변을 `TARGET_SIZE`(기본 1024)에 맞춤. 작은 이미지는 키우지 않음
3. 노이즈 제거 — 3×3 가우시안 블러
4. 화이트밸런스 — gray-world (조명 색 보정)
5. 대비 보정 — LAB의 L 채널에만 CLAHE
6. 후보 영역 검출 — Otsu 이진화 → 열림/닫힘 → 외곽선 → 면적 필터. 큰 순서로 최대 `MAX_CANDIDATES`개
7. (`/frame`만) inference로 전송 — 아래 "inference로 보내기" 참고

## API (초안 — `docs/contracts/`에 합의 후 옮길 예정)

경계별 형식 제안: [contracts/gateway-preprocessing.md](contracts/gateway-preprocessing.md), [contracts/preprocessing-inference.md](contracts/preprocessing-inference.md)

### `GET /health`
`{"service": "preprocessing", "status": "ok"}`

`forward`에 inference 전송 집계(`received` `replaced` `sent` `busy` `errors` `last_ms` `pending`)가 함께 나온다.

### 요청 형식 (`/frame`, `/preprocess` 공통)

둘 중 하나:

- 원본 바이트: `Content-Type: image/jpeg` (또는 `image/png`). 프레임 번호는 `X-Frame-Id` 헤더나 `?frame_id=`, gateway가 받은 시각은 `X-Timestamp` 헤더나 `?timestamp=` (unix 초)
- JSON: `Content-Type: application/json`, `{"frame_id": "123", "timestamp": 1761600000.123, "image": "<base64 JPEG/PNG>"}`. `data:image/jpeg;base64,` 접두어는 있어도 된다

`frame_id`, `timestamp`는 바꾸지 않고 그대로 다음 단계로 넘긴다. `timestamp`는 선택이다.

### `POST /frame` — gateway가 부르는 곳

전처리 후 inference로 보낸다. inference 결과를 기다리지 않고 바로 응답한다.

응답 `202`:

```json
{"frame_id": "123", "status": "queued", "candidates": 2, "elapsed_ms": 12.3}
```

| status | 의미 |
|---|---|
| `queued` | 전송 대기열에 넣었다 |
| `replaced` | 대기 중이던 이전 프레임을 버리고 이 프레임으로 바꿨다 (inference가 바쁠 때 정상) |
| `disabled` | `INFERENCE_URL`이 비어 있어 보내지 않았다 |

#### inference로 보내기

- `POST $INFERENCE_URL`에 아래 `/preprocess` 응답 JSON을 그대로 보낸다. 대희 님 inference `/infer`가 이 형식을 바로 받는다.
- 전송은 스레드 하나가 한 번에 한 프레임씩 한다. inference가 처리 중일 때 들어온 프레임은 **가장 최신 것 하나만** 남긴다. 실시간 화면이라 밀린 옛 프레임보다 지금 프레임이 중요하다.
- inference가 `503`(다른 프레임 처리 중)을 주면 그 프레임은 버리고 다음 프레임을 보낸다. 연결 실패·시간 초과도 로그만 남기고 계속한다.

### `POST /preprocess` — 확인·디버그용

전처리 결과를 그대로 돌려준다. inference로 보내지 않는다.

응답 `200`:

```json
{
  "frame_id": "123",
  "timestamp": 1761600000.123,
  "image": "<base64 JPEG>",
  "original_image": "<base64 JPEG, 색 보정 전>",
  "format": "jpeg",
  "width": 1024, "height": 576,
  "original_width": 1920, "original_height": 1080,
  "scale": 0.5333,
  "candidates": [
    {"bbox": [214, 182, 427, 395], "point": [319.8, 287.8], "area": 35481}
  ],
  "elapsed_ms": 12.3
}
```

- `bbox`(`[x1, y1, x2, y2]`), `point`(`[x, y]`)는 **전처리된 이미지 좌표**다. 원본 좌표 = 값 ÷ `scale`.
- `candidates`가 비어 있으면 화면에서 물체를 찾지 못한 것이다.
- `image`는 SAM 2용(블러·화이트밸런스·CLAHE)이라 색이 실제와 다르다. 대시보드에 보여 줄 이미지는 `original_image`다 (`ORIGINAL_MODE`). inference가 이것을 visualizer로 넘긴다.
- `timestamp`는 요청에 있을 때만 들어간다.

오류: `400` 디코드 실패·형식 오류, `413` 본문 초과(`MAX_BODY_BYTES`, 기본 10MB), `415` 지원하지 않는 Content-Type. 본문은 `{"error": "..."}`.

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `TARGET_SIZE` | 1024 | 리사이즈 후 긴 변 |
| `JPEG_QUALITY` | 90 | 출력 JPEG 품질 |
| `MIN_AREA_RATIO` | 0.002 | 화면 대비 최소 후보 면적 |
| `MAX_CANDIDATES` | 20 | 후보 최대 개수 |
| `MAX_BODY_BYTES` | 10485760 | 요청 본문 최대 크기 |
| `ORIGINAL_MODE` | `resized` | `original_image`: `resized` 리사이즈만 한 이미지(`image`와 같은 크기), `raw` 받은 바이트 그대로, `off` 보내지 않음 |
| `INFERENCE_URL` | `http://inference:8003/infer` | `/frame` 결과를 보낼 곳. 비우면 보내지 않음 |
| `INFERENCE_TIMEOUT` | 10 | inference 응답 대기 (초). CPU SAM 2는 프레임당 1~2초 |

## 실행·확인

```bash
docker compose up --build preprocessing
curl -X POST -H "Content-Type: image/jpeg" -H "X-Frame-Id: 1" --data-binary @frame.jpg http://localhost:8002/preprocess
curl -X POST -H "Content-Type: image/jpeg" -H "X-Frame-Id: 1" -H "X-Timestamp: $(date +%s.%N)" --data-binary @frame.jpg http://localhost:8002/frame
curl http://localhost:8002/health        # 전송 집계 확인
docker compose run --rm --no-deps preprocessing python -m unittest -v test_preprocess test_service
```

`test_service`는 가짜 inference를 띄워 `/frame` 전송, 최신 프레임 우선, `503` 처리를 확인한다.
