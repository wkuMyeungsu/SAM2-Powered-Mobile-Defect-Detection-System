# 추론 결과: inference → visualizer

상태: **제안** (visualizer 작성, inference 확인 필요)

## 요청

`POST http://visualizer:8004/visualize`, `Content-Type: application/json`

```json
{
  "frame_id": 123,
  "timestamp": 1761600000.123,
  "image": "<원본 프레임 JPEG base64>",
  "objects": [
    {
      "contour": [[0.312, 0.420], [0.330, 0.401], [0.351, 0.398]],
      "circularity": 0.62,
      "result": "FAIL"
    }
  ]
}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `frame_id` | int 또는 string | ✅ | gateway가 붙인 번호입니다. 바꾸지 말고 그대로 전달해 주세요. preprocessing처럼 `"123"` 문자열이어도 됩니다. 숫자(또는 숫자 문자열)여야 순서 정리가 됩니다. |
| `timestamp` | float | | gateway가 프레임을 받은 시각(unix 초)입니다. 있으면 그대로 전달해 주세요. 없으면 지연시간만 표시하지 않습니다. |
| `image` | string | ✅ | 프레임 JPEG/PNG base64입니다. preprocessing 결과 이미지를 그대로 보내셔도 됩니다. `data:image/jpeg;base64,` 접두어는 있어도 되고 없어도 됩니다. |
| `width`, `height` | int | | `contour`가 픽셀 좌표일 때, 그 좌표의 기준 이미지 크기입니다. preprocessing 응답의 `width`, `height`를 그대로 넣어 주시면 됩니다. 없으면 `image` 크기를 씁니다. |
| `objects` | list | | 과자 하나당 한 개입니다. 과자가 없으면 `[]`로 보내 주세요. |
| `objects[].contour` | `[[x, y], ...]` | ✅ | 마스크 외곽선입니다. 점은 3개 이상이어야 합니다. **0~1 정규화 좌표**를 권장하고, **픽셀 좌표도 받습니다** (값이 1보다 크면 픽셀 좌표로 봅니다). |
| `objects[].circularity` | float | ✅ | `4π × 면적 / 둘레²` 값입니다. |
| `objects[].result` | `"PASS"` \| `"FAIL"` | ✅ | 판정 결과입니다. |

preprocessing 응답을 받아 그대로 이어 붙이는 경우의 예시입니다.

```json
{
  "frame_id": "123",
  "image": "<preprocessing 이 돌려준 image>",
  "width": 1024, "height": 576,
  "objects": [
    { "contour": [[319, 182], [427, 290], [319, 395], [214, 290]], "circularity": 0.62, "result": "FAIL" }
  ]
}
```

## 확인 부탁드리는 점

1. **이미지**: preprocessing 결과 이미지는 색 보정(화이트밸런스, CLAHE)이 되어 있어서 대시보드에서 실제 색과 다르게 보입니다. 원본을 함께 넘길 수 있으면 원본을 보내 주세요. 어렵다면 preprocessing 결과 이미지로도 동작합니다. (gateway·preprocessing과 함께 정할 부분입니다.)
2. **좌표**: 0~1 좌표와 픽셀 좌표 둘 다 받습니다. 픽셀 좌표라면 그 좌표가 기준으로 하는 이미지 크기를 `width`, `height`로 함께 보내 주세요. 0~1로 바꾸는 코드는 아래와 같습니다.
   ```python
   contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
   c = max(contours, key=cv2.contourArea)
   contour = (c.reshape(-1, 2) / [mask.shape[1], mask.shape[0]]).round(4).tolist()
   ```
3. **점 개수 줄이기**: SAM 2 마스크는 외곽선 점이 많아 전송량이 커질 수 있습니다. 가능하면 `cv2.approxPolyDP`로 점을 줄여서 보내 주세요.
4. **화면 테두리에 걸친 과자**: 잘려 보여서 원형도가 낮게 나옵니다. inference에서는 그대로 판정해서 보내 주시면 되고, visualizer가 테두리에 닿은 윤곽선을 `WAIT`(회색)로 바꿔 표시하고 집계에서 제외하겠습니다.
5. **판정 기준값**: 픽셀 마스크에서 뽑은 윤곽선은 계단 모양이라 `cv2.arcLength`로 재면 둘레가 길게 나옵니다. 그래서 완전한 원도 원형도가 1.0이 아니라 약 0.9가 나옵니다. 시뮬레이터 40프레임(정상 381개, 불량 105개)을 SAM 2처럼 픽셀 마스크로 바꿔 측정한 결과는 아래와 같습니다.

   | 구분 | 원형도 범위 |
   |---|---|
   | 정상 | 0.894 ~ 0.902 |
   | 불량 | 0.581 ~ 0.859 (가장 높은 것은 "단면 절단" 유형) |

   기준값을 PDF처럼 0.95로 잡으면 정상 과자도 전부 FAIL이 됩니다. 시뮬레이터에서는 **0.88 전후**가 두 그룹을 깔끔하게 나눕니다. 실제 SAM 2 마스크로 한 번 더 측정해서 정해 주세요.

## 응답

정상 요청에는 마킹이 끝나는 즉시 200으로 응답합니다. gateway 전송은 visualizer가 뒤에서 따로 하므로, gateway가 느리거나 꺼져 있어도 inference가 기다리지 않습니다. 본문의 `status`는 참고용이니 재전송하지 않으셔도 됩니다.

| status | 의미 |
|---|---|
| `ok` | 마킹을 마치고 gateway 전송 대기열에 넣었습니다. |
| `dropped` | 이미 더 최신 프레임을 처리했기 때문에 버렸습니다. inference를 여러 대 띄우면 정상적으로 발생합니다. |

필드가 빠졌거나 이미지가 깨졌으면 400, 본문이 10MB를 넘으면 413을 응답합니다. 오류 본문은 preprocessing과 같은 `{"error": "..."}` 형식입니다.
