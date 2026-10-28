# 추론 입력: preprocessing → inference

상태: **제안** (preprocessing 작성). inference `README.md`의 `/infer` 형식에 맞췄습니다.

## 요청

`POST http://inference:8003/infer`, `Content-Type: application/json`

preprocessing `/preprocess` 응답 JSON을 그대로 보냅니다.

```json
{
  "frame_id": 123,
  "timestamp": 1761600000.123,
  "image": "<전처리된 JPEG base64 — SAM 2 입력>",
  "original_image": "<색 보정 전 JPEG base64 — 대시보드 표시용>",
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

| 필드 | 설명 |
|---|---|
| `frame_id`, `timestamp` | gateway 값을 그대로 넘깁니다. `timestamp`는 gateway가 보냈을 때만 있습니다. |
| `image` | 리사이즈(긴 변 1024) → 블러 → 화이트밸런스 → CLAHE. SAM 2에 넣는 이미지입니다. |
| `original_image` | 같은 크기로 리사이즈만 한 이미지 (`ORIGINAL_MODE=resized`). inference가 visualizer로 넘겨 실제 색으로 표시합니다. `raw`면 gateway가 보낸 원본 그대로, `off`면 필드가 없습니다. |
| `candidates[].bbox` | `image` 기준 픽셀 `[x1, y1, x2, y2]`. SAM 2 박스 프롬프트입니다. 큰 순서, 최대 20개. |
| `candidates[].point` | 후보 무게중심 `[x, y]`. 점 프롬프트가 필요할 때 씁니다. |
| `scale` | 원본 좌표 = `image` 좌표 ÷ `scale`. |

## 보내는 방식

- 한 번에 한 프레임만 보냅니다. inference가 처리 중일 때 들어온 프레임은 가장 최신 것 하나만 남기고 버립니다.
- `503`(busy)이면 그 프레임은 버리고 다음 프레임을 보냅니다. 재시도하지 않습니다.
- 응답 대기 시간은 `INFERENCE_TIMEOUT`(기본 10초). 시간 초과·연결 실패는 로그만 남깁니다.
- 응답 본문은 쓰지 않습니다 (inference가 visualizer로 직접 보냄).
