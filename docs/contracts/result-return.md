# 결과 재수신: visualizer → gateway → web

- 상태: **초안 (합의 전)**
- 확인: 우진(gateway) · 도은(visualizer) · 명수(web)

## visualizer → gateway

판정을 그려 넣은 프레임과 판정 결과를 gateway로 보낸다.

| 항목 | 값 |
|---|---|
| 메서드·주소 | `POST http://gateway:8001/results` |
| 본문 | `multipart/form-data` |
| `image` 필드 | 판정이 표시된 JPEG |
| `meta` 필드 | JSON `{"session_id": ..., "frame_id": ...}`. gateway가 처음 보낸 `X-Session-Id`, `X-Frame-Id` 헤더 값 |
| `result` 필드 | 판정 결과 JSON 문자열 |

`result`의 내용은 inference → visualizer 계약을 따른다. gateway는 내용을 해석하지 않고 web에 그대로 전달한다. web이 공통으로 쓸 수 있게 최소한 아래 필드를 권장한다.

```json
result (권장 최소 형식)
{
  "verdict": "defect",
  "items": [
    {"id": 1, "verdict": "ok", "circularity": 0.93},
    {"id": 2, "verdict": "defect", "circularity": 0.71}
  ]
}
```

`verdict`: `ok`(모두 정상) · `defect`(불량 있음) · `none`(과자를 찾지 못함)

| 응답 | 뜻 |
|---|---|
| `200 {"delivered": 2}` | 전달됨 (숫자는 결과를 받은 화면 수) |
| `400` | `meta`·`result`가 JSON이 아니거나 `session_id`·`frame_id`가 없음 |
| `404` | 이미 끝난 세션. visualizer는 무시하면 된다 |

## web ↔ gateway

web 화면(브라우저)은 PC에서 `http://localhost:8001`로 gateway를 부른다. gateway는 CORS를 열어 둔다.

| 요청 | 하는 일 |
|---|---|
| `POST /api/sessions` | 세션을 만든다 |
| `GET /api/sessions/<세션 ID>` | 세션 상태 (휴대폰 연결, 처리 수 등) |
| `GET /api/sessions/<세션 ID>/qr.svg` | 휴대폰 접속 QR (SVG). 공개 주소가 준비되기 전에는 503 |
| WebSocket `ws://localhost:8001/ws/view/<세션 ID>` | 원본 미리보기와 판정 결과를 받는다 |

```json
POST /api/sessions → 201
{
  "session_id": "a8f3k2",
  "phone_url": "https://xxxx.trycloudflare.com/phone?s=a8f3k2",
  "qr_url": "http://localhost:8001/api/sessions/a8f3k2/qr.svg",
  "view_url": "ws://localhost:8001/ws/view/a8f3k2",
  "tunnel": {"status": "ready", "url": "https://xxxx.trycloudflare.com", "error": null}
}
```

`phone_url`이 `null`이면 공개 주소를 만드는 중이다. 2초 간격으로 `GET /api/sessions/<세션 ID>`를 다시 불러 값이 생기면 QR을 띄운다.

### 보기 WebSocket 메시지 (gateway → web, 모두 JSON 텍스트)

```json
{"type": "status", "phone_connected": true, "frames_received": 120, "forwarded": 30, "results": 29, "forward_errors": 0, "last_error": null}

{"type": "frame", "frame_id": 120, "image": "data:image/jpeg;base64,..."}

{"type": "result", "stage": "final", "frame_id": 118, "latency_ms": 840, "result": {"verdict": "defect", "items": [...]}, "image": "data:image/jpeg;base64,..."}
```

| type | 언제 | 쓰임 |
|---|---|---|
| `status` | 연결 직후, 상태가 바뀔 때, 2초마다 | 연결 표시, 처리 현황 |
| `frame` | 원본 프레임, 초당 최대 2장 | 실시간 미리보기 |
| `result` | visualizer가 결과를 보낼 때 | 판정 화면. `image`를 `<img src>`에 그대로 넣으면 된다 |

`latency_ms`는 gateway가 원본을 받은 때부터 결과가 돌아올 때까지의 시간이다.

`stage`는 어느 단계의 결과인지 알려 준다.

| stage | 내용 |
|---|---|
| `final` | visualizer가 보낸 최종 판정. `result`는 위 권장 형식 |
| `preprocessing` | 전처리 응답 (뒤 단계가 아직 없을 때). `image`는 전처리된 이미지, `result.candidates`의 `bbox`·`point`는 그 이미지(`result.width`×`result.height`) 기준 좌표 |
| `echo` | gateway 테스트 모드. 받은 원본을 그대로 돌려줌 |

## 정할 것

- [ ] `result` 권장 형식 (도은·대희)
- [ ] web이 `image`를 그대로 쓰는 방식이 편한지 (명수)
- [ ] 원본 미리보기 초당 2장이 적당한지 (명수)
