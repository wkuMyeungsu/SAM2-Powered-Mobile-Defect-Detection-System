# gateway (C1) — QR 접속, 프레임 전달

- 담당: 우진
- 컨테이너 주소: `http://gateway:8001` (호스트 포트 8001)
- 브랜치: `<태그>/gateway/<기능>` → `dev`로 PR

## 하는 일

```
휴대폰 ──(QR 접속, WebSocket으로 JPEG)──▶ gateway ──(최신 프레임 1장씩)──▶ preprocessing ─▶ inference ─▶ visualizer
                                             ▲                                                          │
web 화면 ◀──(WebSocket: 원본 미리보기, 판정 결과)──┴──────────────(POST /results: 판정 표시 프레임 + 결과)────────┘
```

1. web이 세션을 만들면 휴대폰 접속 QR을 준다.
2. 휴대폰이 QR 주소로 들어와 카메라 프레임을 초당 5장 보낸다.
3. 처리 중인 프레임이 없을 때마다 가장 최신 프레임 1장을 preprocessing(`POST /preprocess`, JPEG 본문)으로 보낸다 (SAM 2가 느려도 밀리지 않게).
4. 결과를 web 화면으로 바로 전달한다. 원본 미리보기도 초당 2장 보낸다.
   - preprocessing이 `200`으로 전처리 결과를 바로 주면 그 결과(전처리 이미지, 후보 영역)를 보낸다.
   - 뒤 단계가 생겨 visualizer가 `POST /results`로 최종 판정을 보내면 그 결과를 보낸다.

휴대폰 브라우저는 HTTPS 주소에서만 카메라를 열어 주므로, 컨테이너 안에서 cloudflared 임시 터널을 띄워 공개 HTTPS 주소를 만든다. 계정이나 설정은 필요 없다.

주고받는 형식은 `docs/contracts/`의 `frame-input.md`, `preprocessing-input.md`, `result-return.md` (초안).

## 실행

```bash
docker compose up --build gateway
```

- 점검 화면: http://localhost:8001 → **새 세션 만들기** → 휴대폰으로 QR을 찍고 **촬영 시작**.
- 공개 주소는 시작 후 몇 초 뒤 생긴다. 점검 화면이나 `/health`의 `tunnel.status`가 `ready`가 되면 QR이 나온다.
- 휴대폰 없이: 점검 화면의 **이 PC에서 촬영 화면 열기**로 PC 웹캠을 쓴다 (`localhost`는 HTTPS 없이도 카메라가 열린다).
- 다른 파트 없이: 루트 `.env`에 `GATEWAY_FORWARD_MODE=echo`를 넣으면 받은 프레임을 그대로 결과로 돌려준다. web 화면을 만들 때 쓴다.
- preprocessing과 함께 띄우면(`docker compose up --build gateway preprocessing`) 점검 화면 오른쪽에 전처리 이미지와 후보 영역 상자가 나온다. 진아 님의 `/preprocess`가 dev에 합쳐지기 전에는 dev의 preprocessing이 뼈대라 "응답 501" 오류가 쌓이는 게 정상이다.

### 테스트

실제 휴대폰·터널·다른 파트 없이 돈다.

```bash
docker compose run --rm gateway pytest -q
```

### 환경 변수 (루트 `.env`, 모두 선택)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `GATEWAY_FORWARD_MODE` | `pipeline` | `pipeline`: preprocessing으로 보냄 · `echo`: 받은 프레임을 바로 결과로 (테스트용) · `off` |
| `GATEWAY_FORWARD_URL` | `http://preprocessing:8002/preprocess` | 프레임을 보낼 주소 |
| `GATEWAY_FORWARD_INTERVAL` | `0.2` | 프레임을 보내는 최소 간격 (초) |
| `GATEWAY_FORWARD_TIMEOUT` | `5` | 결과를 기다리는 최대 시간 (초). 지나면 다음 프레임을 보낸다 |
| `GATEWAY_TUNNEL` | `cloudflared` | `off`면 공개 주소를 만들지 않는다 |
| `GATEWAY_PUBLIC_URL` | (없음) | 고정 공개 주소가 있으면 터널 대신 쓴다 (예: 이름 있는 Cloudflare 터널) |
| `GATEWAY_PHONE_FPS` | `5` | 휴대폰이 보내는 초당 장수 |
| `GATEWAY_FRAME_MAX_SIDE` | `1024` | 휴대폰이 보내는 이미지의 긴 변 (px). SAM 2 입력 크기에 맞춤 |
| `GATEWAY_JPEG_QUALITY` | `0.7` | 휴대폰 JPEG 품질 |
| `GATEWAY_PREVIEW_FPS` | `2` | web에 원본 미리보기를 보내는 초당 장수 (`0`이면 안 보냄) |

## 주소

| 주소 | 누가 | 하는 일 |
|---|---|---|
| `GET /health` | compose | 상태 (터널, 전달 방식) |
| `GET /` | 사람 | 점검 화면 |
| `GET /phone?s=<세션>` | 휴대폰 | 촬영 화면 (QR 주소) |
| `WS /ws/phone/<세션>` | 휴대폰 | 프레임 보내기 |
| `POST /api/sessions` | web | 세션 만들기 |
| `GET /api/sessions/<세션>` | web | 세션 상태 |
| `GET /api/sessions/<세션>/qr.svg` | web | 휴대폰 접속 QR |
| `WS /ws/view/<세션>` | web | 원본 미리보기·판정 결과 받기 |
| `POST /results` | visualizer | 판정 결과 돌려주기 |

## 알아 둘 점

- 임시 터널 주소는 실행할 때마다 바뀐다. 매번 QR을 새로 찍으면 된다.
- 주소를 아는 사람은 누구나 접속할 수 있다. 시연용으로만 쓴다.
- `.env`, 토큰은 커밋하지 않는다.
