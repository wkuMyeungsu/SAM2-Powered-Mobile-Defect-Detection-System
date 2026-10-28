# 전처리 입력: gateway → preprocessing

- 상태: **초안 (합의 전)**
- 확인: 우진(gateway) · 진아(preprocessing)

## 요청

gateway가 `http://preprocessing:8002/frames`로 프레임 1장씩 보낸다.

| 항목 | 값 |
|---|---|
| 메서드·경로 | `POST /frames` |
| 본문 | `multipart/form-data` |
| `image` 필드 | JPEG 파일 (휴대폰이 보낸 그대로, 다시 압축하지 않음) |
| `meta` 필드 | JSON 문자열 (아래) |

```json
meta
{
  "session_id": "a8f3k2",
  "frame_id": 42,
  "received_at": 1761620000123,
  "width": 1024,
  "height": 768
}
```

| meta 필드 | 뜻 |
|---|---|
| `session_id` | 어느 휴대폰·화면의 프레임인지 |
| `frame_id` | 세션 안에서 1부터 늘어나는 번호 |
| `received_at` | gateway가 받은 시각 (Unix ms, gateway 시계). 전체 처리 시간 계산에 쓴다 |
| `width`, `height` | 이미지 크기 (px) |

**`meta`는 고치지 말고 inference → visualizer까지 그대로 넘겨 주세요.** visualizer가 결과를 gateway로 돌려보낼 때 `session_id`와 `frame_id`로 어느 화면에 보낼지 찾습니다.

## 응답

- 2xx면 접수된 것으로 본다. 권장: 받자마자 `202 {"accepted": true}`를 돌려주고 처리는 이어서 한다.
- 처리 결과는 이 응답이 아니라 visualizer가 gateway로 보낸다 (`result-return.md`).
- 2xx가 아니거나 연결이 안 되면 gateway는 2초 쉬고 최신 프레임으로 다시 보낸다.

## 흐름 제어 (최신 프레임 우선)

SAM 2 추론은 휴대폰이 보내는 속도(초당 5장)보다 느리므로, gateway는 세션마다 처리 중인 프레임을 **1장만** 둔다.

1. 프레임 1장을 보낸다.
2. 그 프레임의 결과가 visualizer에서 돌아오거나, 5초(`GATEWAY_FORWARD_TIMEOUT`)가 지나면
3. 그 사이 쌓인 프레임 중 **가장 최신 1장**만 보낸다. 나머지는 버린다.

그래서 preprocessing은 대기열을 따로 둘 필요가 없다. 보내는 최소 간격은 `GATEWAY_FORWARD_INTERVAL`(기본 0.2초)이다.

## 정할 것

- [ ] 경로 `/frames`와 `multipart/form-data` 형식 (진아)
- [ ] 응답을 바로(202) 줄지, 처리가 끝난 뒤 줄지. 둘 다 gateway는 동작한다
- [ ] 결과 대기 시간 5초가 충분한지 (대희: SAM 2 한 장 처리 시간)
