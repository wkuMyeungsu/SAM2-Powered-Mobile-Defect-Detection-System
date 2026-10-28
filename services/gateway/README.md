# gateway (C1) — QR 접속, 프레임 전달

- 담당: 우진
- 컨테이너 주소: `http://gateway:8001` (호스트 포트 8001)
- 팀 구조도(`docs/architecture.html`)의 ① ② ⑤ ⑥ 연결을 맡는다. ③ ④는 preprocessing → inference → visualizer가 이어서 보낸다.

## 하는 일

```
휴대폰 ─①(QR 접속, WebSocket JPEG)─▶ gateway ─②(POST /frame)─▶ preprocessing ─③─▶ inference ─④─▶ visualizer
                                         ▲                                                              │
web 대시보드 ◀──⑥(WebSocket /ws/view)────┴─────────────────────⑤(POST /result)──────────────────────────┘
```

1. 휴대폰이 QR 주소로 들어와 카메라 프레임(JPEG)을 초당 5장 보낸다. 휴대폰 카메라는 HTTPS 주소에서만 열리므로, 컨테이너 안에서 cloudflared 임시 터널로 공개 HTTPS 주소를 만든다 (계정·설정 필요 없음).
2. 프레임마다 `frame_id`와 받은 시각 `timestamp`(unix 초)를 붙인다. `frame_id`는 gateway 전체에서 1씩 늘고, 시작값을 시각에서 정해서 gateway를 다시 켜도 작아지지 않는다 (visualizer는 번호가 작아진 프레임을 오래된 것으로 보고 버린다).
3. 프레임을 preprocessing `POST /frame`에 JPEG 그대로 보낸다 (`X-Frame-Id`, `X-Timestamp` 헤더). preprocessing은 전처리 뒤 inference로 넘기고 바로 `202`로 답한다. 세션마다 한 번에 1장만 보내고, 응답을 기다리는 동안 들어온 프레임은 가장 최신 1장만 남긴다. inference가 느린 것은 preprocessing이 최신 프레임만 남겨 처리한다.
4. visualizer가 `POST /result`로 보낸 결과를 web 대시보드(`/ws/view`)에 그대로 보낸다.

형식: `docs/contracts/frame-input.md`(①), `services/preprocessing/contracts/gateway-preprocessing.md`(②), `docs/contracts/result-return.md`(⑤⑥).

## 실행

```bash
docker compose up --build gateway preprocessing inference visualizer web
```

- 휴대폰 연결: web 대시보드에 QR을 넣거나(`<img src="http://<호스트>:8001/qr.svg">`), gateway 점검 화면 http://localhost:8001 에서 **새 세션 만들기**로 QR을 띄운다. 휴대폰으로 찍고 **촬영 시작**.
- 공개 주소는 시작 후 몇 초 뒤 생긴다. 그 전에는 QR 주소가 `503`을 돌려준다. `/health`의 `tunnel.status`가 `ready`가 되면 된다.
- 휴대폰 없이: 점검 화면의 **이 PC에서 촬영 화면 열기**로 PC 웹캠을 쓴다 (`localhost`는 HTTPS 없이도 카메라가 열린다).
- 다른 파트 없이: 루트 `.env`에 `GATEWAY_FORWARD_MODE=echo`를 넣으면 받은 프레임을 visualizer 결과 형식으로 바로 돌려준다. web 화면을 만들 때 쓴다.
- 전처리 결과만 확인: `GATEWAY_FORWARD_URL=http://preprocessing:8002/preprocess`로 두면 점검 화면에 전처리 이미지와 후보 영역 상자가 나온다 (inference로는 보내지 않는다).

### 테스트

실제 휴대폰·터널·다른 파트 없이 돈다.

```bash
docker compose run --rm --no-deps gateway pytest -q
```

### 환경 변수 (루트 `.env`, 모두 선택)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `GATEWAY_FORWARD_MODE` | `pipeline` | `pipeline`: preprocessing으로 보냄 · `echo`: 받은 프레임을 바로 결과로 (테스트용) · `off` |
| `GATEWAY_FORWARD_URL` | `http://preprocessing:8002/frame` | preprocessing 주소 |
| `GATEWAY_FORWARD_TIMEOUT` | `5` | preprocessing 응답을 기다리는 최대 시간 (초) |
| `GATEWAY_FORWARD_INTERVAL` | `0.2` | 프레임을 보내는 최소 간격 (초) |
| `GATEWAY_TUNNEL` | `cloudflared` | `off`면 공개 주소를 만들지 않는다 |
| `GATEWAY_PUBLIC_URL` | (없음) | 고정 공개 주소가 있으면 터널 대신 쓴다 (예: 이름 있는 Cloudflare 터널) |
| `GATEWAY_PHONE_FPS` | `5` | 휴대폰이 보내는 초당 장수 |
| `GATEWAY_FRAME_MAX_SIDE` | `1024` | 휴대폰이 보내는 이미지의 긴 변 (px). SAM 2 입력 크기에 맞춤 |
| `GATEWAY_JPEG_QUALITY` | `0.7` | 휴대폰 JPEG 품질 |
| `GATEWAY_PREVIEW_FPS` | `2` | 점검 화면에 원본 미리보기를 보내는 초당 장수 (`0`이면 안 보냄) |

## 주소

| 주소 | 누가 | 하는 일 |
|---|---|---|
| `GET /health` | compose | 상태 (터널, 전달 방식, 연결된 대시보드 수) |
| `GET /qr.svg` | web | 기본 세션의 휴대폰 접속 QR (SVG) |
| `GET /api/default-session` | web | 기본 세션 상태 (휴대폰 연결, 처리 수) |
| `WS /ws/view` | web | ⑥ visualizer 결과 JSON을 그대로 받기 |
| `POST /result` | visualizer | ⑤ 마킹 프레임과 판정 보내기 (`frame_id`가 null이면 대시보드에만 전달) |
| `GET /phone?s=<세션>` | 휴대폰 | 촬영 화면 (QR 주소) |
| `WS /ws/phone/<세션>` | 휴대폰 | ① 프레임 보내기 |
| `GET /` | 사람 | gateway 점검 화면 (QR, 원본, 결과, 처리 현황) |
| `POST /api/sessions` | 점검 화면 | 새 세션 만들기 |
| `GET /api/sessions/<세션>`, `/api/sessions/<세션>/qr.svg` | 점검 화면 | 세션 상태, QR |
| `WS /ws/view/<세션>` | 점검 화면 | 세션의 status·frame·result 메시지 |

## 알아 둘 점

- 임시 터널 주소는 실행할 때마다 바뀐다. 매번 QR을 새로 찍으면 된다.
- 주소를 아는 사람은 누구나 접속할 수 있다. 시연용으로만 쓴다.
- `.env`, 토큰은 커밋하지 않는다.
