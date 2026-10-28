# 전처리 입력: gateway → preprocessing

상태: **제안** (preprocessing 작성, gateway 확인 필요)

## 요청

`POST http://preprocessing:8002/frame`

둘 중 편한 쪽으로 보내 주세요.

**① 원본 바이트 (권장 — base64 변환이 없어 가볍습니다)**

```
Content-Type: image/jpeg        (또는 image/png)
X-Frame-Id: 123
X-Timestamp: 1761600000.123
<JPEG 바이트>
```

**② JSON**

```json
{"frame_id": 123, "timestamp": 1761600000.123, "image": "<JPEG/PNG base64>"}
```

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `frame_id` | int 또는 string | 권장 | gateway가 붙인 번호. 바꾸지 않고 inference·visualizer까지 그대로 갑니다. visualizer가 순서 정리에 쓰므로 숫자(또는 숫자 문자열)로 늘어나게 붙여 주세요. |
| `timestamp` | float | | gateway가 프레임을 받은 시각(unix 초). visualizer가 전체 지연시간을 잽니다. 없으면 지연시간만 표시하지 않습니다. |
| `image` | JPEG/PNG | ✅ | 해상도 제한은 없지만 긴 변 1024로 줄이므로 그보다 크게 보낼 필요는 없습니다. 본문 10MB까지. |

## 응답

전처리만 끝나면 inference 결과를 기다리지 않고 바로 `202`로 응답합니다 (보통 수십 ms). 재전송할 필요는 없습니다.

```json
{"frame_id": 123, "status": "queued", "candidates": 2, "elapsed_ms": 12.3}
```

| status | 의미 |
|---|---|
| `queued` | inference 전송 대기열에 넣었습니다. |
| `replaced` | inference가 바빠서 대기 중이던 이전 프레임을 이 프레임으로 바꿨습니다. 정상입니다. |
| `disabled` | inference 전송이 꺼져 있습니다 (`INFERENCE_URL` 비어 있음). |

오류는 `{"error": "..."}` 본문과 함께 `400`(이미지·JSON·timestamp 형식), `413`(10MB 초과), `415`(Content-Type)입니다.

## 확인 부탁드리는 점

1. 두 방식 중 어느 쪽으로 보낼지.
2. 프레임을 보내는 주기. preprocessing은 빨리 받아도 되지만 inference(CPU)가 프레임당 1~2초라, 그 사이 프레임은 최신 것만 남고 버려집니다.
