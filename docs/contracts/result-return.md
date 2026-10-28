# 결과 재수신: visualizer → gateway → web (구조도 ⑤ ⑥)

- 상태: **확인됨**. 도은 님 형식(`services/visualizer/contracts/visualizer-gateway.md`, 확인됨)을 gateway가 그대로 받고, 명수 님 대시보드가 `/ws/view`·`/qr.svg`·`/api/default-session`을 쓴다. gateway → 전처리 → 추론 → visualizer → gateway → 대시보드 연결 테스트 통과.
- 확인: 우진(gateway) · 도은(visualizer) · 명수(web)

## ⑤ visualizer → gateway

제안 그대로다: `POST http://gateway:8001/result`, JSON `{frame_id, timestamp, latency_ms, image, summary, objects}`.

| 응답 | 뜻 |
|---|---|
| `200 {"delivered": 2}` | 전달됨 (숫자는 결과를 받은 화면 수) |
| `400` | `image`가 없음 |

`frame_id`가 `null`이면(inference가 번호를 빠뜨린 경우) 대시보드에만 전달한다. `objects[].reason` 같은 필드도 손대지 않고 그대로 넘긴다.

## ⑥ gateway → web

| 항목 | 값 |
|---|---|
| 주소 | `ws://<호스트>:8001/ws/view` |
| 내용 | ⑤의 JSON을 **그대로** 텍스트 메시지로 보낸다 (`image`는 `data:` 접두어 없는 base64) |
| 연결 직후 | 마지막 결과가 있으면 한 번 바로 보낸다 |

## web이 쓸 수 있는 것 (gateway 추가)

| 주소 | 하는 일 |
|---|---|
| `GET http://<호스트>:8001/qr.svg` | 휴대폰 접속 QR (SVG). `<img src>`에 그대로 넣는다. 공개 주소를 만드는 몇 초 동안은 `503`이므로 실패하면 2초 뒤 다시 불러온다 |
| `GET http://<호스트>:8001/api/default-session` | 휴대폰 연결 여부(`phone_connected`), 받은·처리한 프레임 수(`frames_received`, `forwarded`), 오류(`forward_errors`, `last_error`), 터널 상태(`tunnel`) |

기본 세션은 오래 쓰지 않아도 지우지 않는다. 대시보드에 띄운 QR이 끝난 세션을 가리키지 않게 하기 위해서다.

## 도은 님 질문에 대한 답

1. `/result` 경로로 받는다.
2. gateway가 프레임을 받을 때 `frame_id`와 `timestamp`(unix 초)를 붙여 inference에 넘긴다. preprocessing은 거치지 않아도 된다 (gateway가 직접 inference에 넘김, `inference-input.md`). 원본 이미지도 `original_image`로 함께 넘긴다.
3. web으로 JSON을 그대로 넘긴다.
4. WebSocket 주소는 `ws://<호스트>:8001/ws/view`.
