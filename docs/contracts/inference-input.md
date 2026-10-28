# 추론 입력: gateway → inference (구조도 ③)

- 상태: **초안**. 대희 님이 구현한 `POST /infer` 형식(`services/inference/README.md`)을 따른다.
- 확인: 우진(gateway) · 대희(inference) · 도은(visualizer)

## 누가 부르나

preprocessing은 결과를 응답으로 돌려주므로 **gateway가 그 응답을 받아 inference를 부른다.** inference는 visualizer로 보내고(④), visualizer가 gateway로 돌려준다(⑤).

## 요청

preprocessing 응답 JSON을 그대로 넣고 세 필드를 덧붙인다.

```json
POST http://inference:8003/infer
Content-Type: application/json

{
  "frame_id": "42",
  "timestamp": 1761620000.123,
  "original_image": "<휴대폰이 보낸 원본 JPEG base64>",

  "image": "<preprocessing이 돌려준 전처리 이미지 base64>",
  "width": 1024, "height": 576,
  "original_width": 1024, "original_height": 576, "scale": 1.0,
  "candidates": [{"bbox": [214, 182, 427, 395], "point": [319.8, 287.8], "area": 35481}],
  "format": "jpeg", "elapsed_ms": 12.3
}
```

| 필드 | 뜻 |
|---|---|
| `frame_id` | gateway 번호 (문자열, preprocessing 응답과 같은 값). visualizer까지 그대로 넘긴다 |
| `timestamp` | gateway가 프레임을 받은 시각 (unix 초). visualizer가 전체 지연시간을 잰다 |
| `original_image` | 원본 프레임. 전처리 이미지는 색 보정이 되어 있어서, visualizer가 원본 위에 그리도록 넘긴다 |

## 응답

| 응답 | gateway 동작 |
|---|---|
| `200` | 다음 최신 프레임으로 넘어간다. 결과 화면은 visualizer가 `POST /result`로 보낸 것으로 그린다 |
| `503` (이전 프레임 처리 중) | 이 프레임은 건너뛰고 다음 최신 프레임을 보낸다. 오류로 세지 않는다 |
| 그 밖, 연결 실패 | 오류로 기록하고 2초 뒤 다시 보낸다. 점검 화면에는 전처리 결과를 대신 보여 준다 |

gateway는 inference 응답을 최대 15초(`GATEWAY_INFERENCE_TIMEOUT`) 기다린다.
