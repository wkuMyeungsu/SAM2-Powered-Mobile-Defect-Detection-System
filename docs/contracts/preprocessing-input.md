# 전처리 입력: gateway → preprocessing

- 상태: **초안 (합의 전)**. 진아 님이 구현한 `POST /preprocess`(`feature/preprocessing/frame-preprocess`) 형식에 맞췄다.
- 확인: 우진(gateway) · 진아(preprocessing)

## 요청

gateway가 프레임 1장씩 보낸다. 본문은 휴대폰이 보낸 JPEG 그대로다 (다시 압축하지 않음).

```
POST http://preprocessing:8002/preprocess
Content-Type: image/jpeg
X-Frame-Id: 42
X-Session-Id: a8f3k2
X-Received-At: 1761620000123

<JPEG 바이트>
```

| 헤더 | 뜻 |
|---|---|
| `X-Frame-Id` | 세션 안에서 1부터 늘어나는 번호. preprocessing이 응답의 `frame_id`로 돌려준다 |
| `X-Session-Id` | 어느 휴대폰·화면의 프레임인지 (지금 preprocessing은 쓰지 않음) |
| `X-Received-At` | gateway가 받은 시각 (Unix ms). 전체 처리 시간 계산용 (지금 preprocessing은 쓰지 않음) |

## 응답

preprocessing은 처리 결과를 바로 `200`으로 돌려준다 (형식은 `services/preprocessing/README.md`).

```json
{
  "frame_id": "42",
  "image": "<base64 JPEG, 전처리된 이미지>",
  "format": "jpeg",
  "width": 1024, "height": 576,
  "original_width": 1024, "original_height": 576,
  "scale": 1.0,
  "candidates": [{"bbox": [214, 182, 427, 395], "point": [319.8, 287.8], "area": 35481}],
  "elapsed_ms": 12.3
}
```

gateway는 응답에 따라 이렇게 한다.

| 응답 | gateway 동작 |
|---|---|
| `200` + JSON에 `image` 있음 | 전처리 결과를 web 화면에 바로 보낸다 (`stage: "preprocessing"`). 다음 최신 프레임으로 넘어간다 |
| `202` 등 본문 없는 2xx | 뒤 단계(visualizer)가 `POST /results`로 최종 결과를 보낼 때까지 기다린다 |
| 2xx가 아님, 연결 실패 | 오류로 기록하고 2초 쉰 뒤 최신 프레임으로 다시 보낸다 |

## 흐름 제어 (최신 프레임 우선)

gateway는 세션마다 처리 중인 프레임을 **1장만** 둔다. 결과가 오거나 5초(`GATEWAY_FORWARD_TIMEOUT`)가 지나면, 그 사이 쌓인 프레임 중 **가장 최신 1장**만 보낸다. 그래서 preprocessing은 대기열을 따로 둘 필요가 없다. 보내는 최소 간격은 `GATEWAY_FORWARD_INTERVAL`(기본 0.2초)이다.

## 정할 것

- [ ] **inference는 누가 부르나** (진아·대희·도은·명수). 전처리가 결과를 바로 응답하는 구조라 둘 중 하나를 정해야 한다.
  - 방식 A, gateway가 차례로 부름: gateway → preprocessing → gateway → inference → gateway → visualizer. 각 파트는 요청을 받아 응답만 하면 된다. gateway에 순서 관리가 모인다.
  - 방식 B, 각 단계가 다음 단계를 부름: preprocessing이 응답 대신(또는 응답과 함께) inference로 넘기고, 마지막 visualizer가 `POST /results`로 gateway에 돌려준다. 이때 `X-Frame-Id`, `X-Session-Id`를 다음 단계로 그대로 넘겨야 한다.
  - 지금 gateway는 두 방식 모두 받을 수 있다 (위 응답 표).
- [ ] 결과 대기 시간 5초가 충분한지 (대희: SAM 2 한 장 처리 시간)
