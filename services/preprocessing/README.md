# preprocessing (C2) — 전처리

gateway에서 받은 프레임을 SAM 2 추론에 맞게 다듬고, 과자 후보 영역을 찾아 inference에 넘길 프롬프트(박스·점)로 돌려준다.

## 처리 순서

1. 디코드 (JPEG/PNG)
2. 리사이즈 — 긴 변을 `TARGET_SIZE`(기본 1024)에 맞춤. 작은 이미지는 키우지 않음
3. 노이즈 제거 — 3×3 가우시안 블러
4. 화이트밸런스 — gray-world (조명 색 보정)
5. 대비 보정 — LAB의 L 채널에만 CLAHE
6. 후보 영역 검출 — Otsu 이진화 → 열림/닫힘 → 외곽선 → 면적 필터. 큰 순서로 최대 `MAX_CANDIDATES`개

## API (초안 — `docs/contracts/`에 합의 후 옮길 예정)

### `GET /health`
`{"service": "preprocessing", "status": "ok"}`

### `POST /preprocess`

요청은 둘 중 하나:

- 원본 바이트: `Content-Type: image/jpeg` (또는 `image/png`), 프레임 번호는 `X-Frame-Id` 헤더나 `?frame_id=` 로
- JSON: `Content-Type: application/json`, `{"frame_id": "123", "image": "<base64 JPEG/PNG>"}`

응답 `200`:

```json
{
  "frame_id": "123",
  "image": "<base64 JPEG>",
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

오류: `400` 디코드 실패·형식 오류, `413` 본문 초과(`MAX_BODY_BYTES`, 기본 10MB), `415` 지원하지 않는 Content-Type. 본문은 `{"error": "..."}`.

## 환경 변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| `TARGET_SIZE` | 1024 | 리사이즈 후 긴 변 |
| `JPEG_QUALITY` | 90 | 출력 JPEG 품질 |
| `MIN_AREA_RATIO` | 0.002 | 화면 대비 최소 후보 면적 |
| `MAX_CANDIDATES` | 20 | 후보 최대 개수 |
| `MAX_BODY_BYTES` | 10485760 | 요청 본문 최대 크기 |

## 실행·확인

```bash
docker compose up --build preprocessing
curl -X POST -H "Content-Type: image/jpeg" -H "X-Frame-Id: 1" --data-binary @frame.jpg http://localhost:8002/preprocess
docker compose run --rm preprocessing python -m unittest -v test_preprocess
```
