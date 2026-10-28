# 주머니 속의 스마트 팩토리

SAM 2 및 모바일 스트리밍 기반 실시간 불량 과자 검출 시스템.
스마트폰 카메라가 보낸 프레임을 4단계 컨테이너(C1~C4)가 처리하고, 웹 대시보드에 불량 판정 결과를 표시한다.

## 데이터 흐름

```
휴대폰 ──①──▶ gateway ──②──▶ preprocessing ──③──▶ inference ──④──▶ visualizer
 (QR 접속)     (C1)            (C2)                 (C3)              (C4)
                 │                                                     │
                 ◀──────────────────────────⑤──────────────────────────┘
                 │
                 └──⑥──▶ web 대시보드
```

| 번호 | 구간 | 방식 | 형식 문서 |
|---|---|---|---|
| ① | 휴대폰 → gateway | WebSocket `/ws/phone/<세션>`, 바이너리 JPEG (Cloudflare 임시 터널 경유) | `docs/contracts/frame-input.md` |
| ② | gateway → preprocessing | `POST /frame` | `services/preprocessing/contracts/gateway-preprocessing.md` |
| ③ | preprocessing → inference | `POST /infer` | `services/preprocessing/contracts/preprocessing-inference.md` |
| ④ | inference → visualizer | `POST /visualize` | `services/visualizer/contracts/inference-visualizer.md` |
| ⑤ | visualizer → gateway | `POST /result` | `services/visualizer/contracts/visualizer-gateway.md` |
| ⑥ | gateway → web | WebSocket `/ws/view` | `docs/contracts/result-return.md` |

gateway는 프레임마다 `frame_id`와 받은 시각 `timestamp`를 붙이고, 뒤 단계는 이 값을 그대로 넘긴다. preprocessing이 바쁜 동안 들어온 프레임은 최신 1장만 남기고 버린다.

### 휴대폰 접속 (Cloudflare 임시 터널)

휴대폰 브라우저는 HTTPS 주소에서만 카메라를 열어 준다. 그래서 gateway 컨테이너 안에서 `cloudflared`가 함께 실행되어, 실행한 PC의 gateway(8001)로 이어지는 공개 HTTPS 주소 `https://<임의 이름>.trycloudflare.com`을 만든다. 대시보드 QR에는 이 주소가 들어간다.

```
휴대폰 ──HTTPS──▶ Cloudflare ◀── cloudflared (실행한 PC의 gateway 컨테이너 안) ──▶ gateway :8001
```

- `cloudflared`가 실행한 PC에서 Cloudflare로 먼저 연결하므로 공유기 포트 개방이나 고정 IP가 필요 없고, 휴대폰이 PC와 다른 망(LTE 등)에 있어도 접속된다. 계정과 설정도 필요 없다.
- 각자 자기 PC에서 `docker compose up`을 하면 그 PC에 따로 터널이 생긴다. 처리는 모두 실행한 PC에서 하고, Cloudflare는 휴대폰과 gateway 사이(①)만 전달한다. ②~⑥은 Docker 내부망에서 오간다.
- 실행한 PC가 인터넷에 연결되어 있어야 한다.
- 주소는 실행할 때마다 바뀐다. 매번 대시보드의 새 QR을 찍는다.
- 주소를 아는 사람은 누구나 접속할 수 있다. 시연용으로만 쓴다.
- 터널 상태는 gateway `/health`의 `tunnel`(`starting` → `ready`)에 나온다. 주소가 생기기 전까지 QR 주소는 `503`을 돌려준다.
- 터널을 끄려면 `.env`에 `GATEWAY_TUNNEL=off`를 넣는다. 이때는 PC 웹캠으로만 찍을 수 있다(`localhost`는 HTTPS 없이도 카메라가 열린다). 직접 마련한 HTTPS 주소가 있으면 `GATEWAY_PUBLIC_URL`에 넣는다.

## 구조 · 담당 · 포트

| 폴더 | 역할 | 담당 | 호스트 포트 | 컨테이너 주소 |
|---|---|---|---|---|
| `web/` | 대시보드 | 명수 | 8000 | `http://web:8000` |
| `services/gateway/` | QR 접속, 프레임 수신·전달, 결과 재전송 (C1) | 우진 | 8001 | `http://gateway:8001` |
| `services/preprocessing/` | 리사이즈·색 보정·과자 후보 영역 검출 (C2) | 진아 | 8002 | `http://preprocessing:8002` |
| `services/inference/` | SAM 2 분할, 원형도 기반 불량 판정 (C3) | 대희 | 8003 | `http://inference:8003` |
| `services/visualizer/` | 판정 마킹, 과자 추적·누적 집계 (C4) | 도은 | 8004 | `http://visualizer:8004` |
| `tests/e2e/` | 전체 연결 테스트 | 공통 | - | - |

서비스별 API·환경 변수·단독 실행 방법은 각 폴더의 `README.md`에 있다.

### web 대시보드

`web/index.html` 한 장을 nginx가 제공한다. 브라우저가 접속한 주소의 호스트 이름으로 gateway(8001)와 visualizer(8004)에 직접 붙는다.

| 화면 | 쓰는 주소 |
|---|---|
| 검사 화면 (마킹된 프레임), 집계, 현재 프레임 판정 | gateway `ws://<호스트>:8001/ws/view` |
| 휴대폰 연결 QR, 연결 상태 | gateway `/qr.svg`, `/api/default-session` |
| 성능 (최근 10초 fps·지연, 처리·버림·오류 수) | visualizer `/metrics` |
| 누적 초기화 버튼 | visualizer `POST /reset` |
| gateway에 연결되지 않았을 때 검사 화면·집계 | visualizer `/latest.jpg`, `/health` |

## 실행

필요한 것: Docker Desktop (실행 중일 것), Docker Compose 2.24 이상.

```bash
docker compose up --build -d           # 전체
docker compose up --build web          # 내 서비스만
docker compose ps                      # 상태 (모두 healthy인지 확인)
docker compose down                    # 종료
```

- 첫 빌드는 오래 걸린다. inference 이미지는 CPU용 PyTorch와 SAM 2 가중치(`facebook/sam2.1-hiera-tiny`)를 포함해 약 2GB다. 코드만 바꿔 다시 빌드하면 이 층은 캐시를 쓴다.
- 호스트 포트가 다른 프로그램과 겹치면 `.env.example`을 `.env`로 복사해 포트만 바꾼다. `.env`가 없어도 기본값으로 실행된다.
- 컨테이너 안에서 다른 서비스는 `localhost`가 아니라 서비스 이름으로 부른다 (`http://inference:8003`).

### 시연 순서

1. `docker compose up --build -d`로 전체를 띄우고 `docker compose ps`에서 모두 healthy인지 확인한다.
2. PC 브라우저에서 `http://localhost:8000`을 연다. Cloudflare 터널 주소가 생기면(몇 초) QR이 나타난다.
3. 대시보드의 QR을 휴대폰으로 찍고, 열린 페이지에서 카메라 권한을 허용한 뒤 "촬영 시작"을 누른다.
4. 대시보드에 마킹된 프레임과 PASS / FAIL 집계가 표시된다. "누적 초기화"로 누적 수를 0으로 되돌린다.

## 테스트

### 서비스별 단위 테스트

| 서비스 | 명령 |
|---|---|
| gateway | `docker compose run --rm --no-deps gateway python -m pytest -q` |
| preprocessing | `docker compose run --rm --no-deps preprocessing python -m unittest -v test_preprocess test_service` |
| inference | `docker compose run --rm --no-deps inference python -m unittest -v test_inference` |
| visualizer | `docker compose run --rm --no-deps visualizer python -m unittest -v test_visualizer` |

### 전체 연결 테스트 (컨테이너 전체)

```bash
docker compose --profile test run --rm --build e2e
```

5개 서비스가 healthy가 될 때까지 기다린 뒤, `e2e` 컨테이너가 Docker 내부망에서(터널을 거치지 않고) 휴대폰 대신 합성 컨베이어 영상(정상 과자 8개, 불량 과자 4개)을 초당 5장씩 20초 동안 gateway로 보내고, 대시보드 자리(`/ws/view`)에서 결과를 받아 확인한다. 끝나면 컨테이너는 지워지고 5개 서비스는 계속 떠 있다.

| 확인 항목 | 통과 기준 |
|---|---|
| health | gateway·preprocessing·inference·visualizer `/health`가 `ok`, web 페이지 응답 |
| 연결 | gateway가 촬영 설정(`config`)을 보내고, 대시보드가 결과를 1건 이상 받음 |
| 순서 | 받은 결과의 `frame_id`가 오름차순이고 중복 없음 |
| 형식 | 모든 결과에 마킹 이미지가 있고, PASS와 FAIL 판정이 모두 나옴 |
| 오류 | gateway 전달 오류, preprocessing → inference 오류, visualizer → gateway 오류가 모두 0 |
| 누적 집계 | 검사선을 지난 과자 6개, 불량 2개와 일치하는지 표시 (어긋나면 WARN만 출력하고 실패로 치지 않음) |

하나라도 실패하면 종료 코드 1로 끝난다. 마지막 입력 프레임과 마킹 결과는 `tests/e2e/out/`에 저장된다(커밋하지 않음). 테스트는 visualizer 누적 집계를 초기화하고 기본 세션에 휴대폰으로 접속하므로, 시연 중에는 돌리지 않는다.

## 현재 성능과 한계

전체 연결 테스트 기준 (CPU 추론, 1024×576 프레임, 과자 12개).

| 항목 | 값 |
|---|---|
| 결과 처리 속도 | 약 0.45~0.65 fps (병목: inference의 SAM 2 CPU 추론, 프레임당 약 2.4초) |
| 지연 (gateway 수신 → visualizer 완료) | 중앙값 약 2.4초 |
| visualizer 마킹 | 프레임당 약 3ms |

- 처리된 두 프레임 사이에 과자가 화면 폭의 15%(visualizer `MAX_JUMP`)보다 많이 움직이면 같은 과자로 이어 붙이지 못해 누적 집계가 적게 나온다. 지금 처리 속도에서는 벨트가 느려야 집계가 맞는다.
- 누적 집계는 과자가 화면 오른쪽에서 왼쪽으로 움직인다고 가정한다.
- visualizer는 누적 수와 프레임 순서를 메모리에 들고 있어서 1개만 띄운다. 재시작하면 누적 수가 0이 된다.
- 휴대폰의 화면 자동 회전이 꺼진 상태에서 가로로 찍으면 프레임이 90도 돌아간 채로 전송된다. 휴대폰 페이지가 기기 방향을 읽지 않고 브라우저가 알려 주는 화면 방향으로 찍기 때문이다.
- Nginx를 이용한 inference Scale-out은 아직 구성하지 않았다.

## 브랜치

- `main`: 최종본. `dev`: 통합. 둘 다 직접 push하지 않고 PR로만 합친다.
- 작업 브랜치: `<태그>/<파트>/<기능>` — 태그는 `feature` `fix` `docs` `refactor` `chore`, 파트는 `web` `gateway` `preprocessing` `inference` `visualizer` `common`.
  예: `feature/web/qr-page`, `fix/inference/empty-mask`
- 작업 브랜치는 `dev`에서 만들고 `dev`로 PR을 올린다. 병합 후 삭제한다.
- 커밋 메시지: `<타입>(<파트>): <요약>`. 예: `feat(web): QR 접속 화면 추가`
- PR 병합은 Create a merge commit만 사용한다.
- `.env`, 비밀키, 모델 가중치(`*.pt` `*.onnx`)는 커밋하지 않는다.
- 전체 절차는 `docs/git-rules.html`.
