# 주머니 속의 스마트 팩토리

SAM 2 및 모바일 스트리밍 기반 실시간 불량 과자 검출 시스템.
휴대폰 카메라 프레임을 4단계 컨테이너(C1~C4)가 처리하고, 웹 대시보드에 불량 판정 결과를 표시한다.

## 구조

```
휴대폰 ──①──▶ gateway ──②──▶ preprocessing ──③──▶ inference ──④──▶ visualizer
                 ▲  │                                                   │
                 │  └──⑥──▶ web 대시보드                                │
                 └──────────────────────⑤───────────────────────────────┘
```

| 폴더 | 역할 | 담당 | 포트 |
|---|---|---|---|
| `web/` | 대시보드 | 명수 | 8000 |
| `services/gateway/` | QR 접속, 프레임 수신·전달, 결과 재전송 (C1) | 우진 | 8001 |
| `services/preprocessing/` | 리사이즈·색 보정·과자 후보 검출 (C2) | 진아 | 8002 |
| `services/inference/` | SAM 2 분할, 불량 판정 (C3) | 대희 | 8003 |
| `services/visualizer/` | 판정 마킹, 추적·누적 집계 (C4) | 도은 | 8004 |

- 서비스별 API·설정·단독 실행: 각 폴더의 `README.md`
- 구간별 데이터 형식(①~⑥): `docs/contracts/README.md`
- 컨테이너끼리는 서비스 이름으로 부른다 (`http://inference:8003`)

## 실행

Docker Desktop과 Docker Compose 2.24 이상이 필요하다.

```bash
docker compose up --build -d     # 전체 실행
docker compose ps                # 모두 healthy인지 확인
docker compose down              # 종료
```

1. PC 브라우저에서 `http://localhost:8000`을 연다.
2. 대시보드의 QR을 휴대폰으로 찍고 "촬영 시작"을 누른다.
3. 대시보드에 마킹된 프레임과 PASS / FAIL 집계가 나온다.

- 휴대폰 카메라는 HTTPS에서만 열리므로, gateway가 Cloudflare 임시 터널로 HTTPS 주소를 만들어 QR에 넣는다. 실행한 PC에 인터넷이 필요하고, 주소는 실행할 때마다 바뀐다 (자세히: `services/gateway/README.md`).
- 첫 빌드는 오래 걸린다. inference 이미지가 약 2GB다 (CPU용 PyTorch + SAM 2 가중치).
- 포트가 겹치면 `.env.example`을 `.env`로 복사해 바꾼다.

## 테스트

| 종류 | 방법 |
|---|---|
| 서비스별 단위 테스트 | 각 서비스 `README.md` |
| 휴대폰 없이 서비스 연결 확인 (②~⑥) | `docker compose --profile test run --rm --build pipeline` · `tests/pipeline/README.md` |
| 실제 휴대폰으로 전 구간 확인 (①~⑥) | `docs/phone-checklist.md` |

## 현재 한계

- 처리 속도 약 0.5 fps, 지연 약 2.4초. inference가 SAM 2를 CPU로 돌리는 것이 병목이다.
- 과자가 빨리 지나가면 누적 집계가 실제보다 적게 나온다. 누적 집계는 과자가 오른쪽 → 왼쪽으로 움직인다고 가정한다.
- 휴대폰 자동 회전이 꺼진 채 가로로 찍으면 영상이 90도 돌아간다.
- Nginx Scale-out은 아직 구성하지 않았다.

## 브랜치

- `main`: 최종본, `dev`: 통합. 둘 다 PR로만 합친다.
- 작업 브랜치는 `dev`에서 `<태그>/<파트>/<기능>`으로 만든다. 예: `feature/web/qr-page`, `fix/inference/empty-mask`
- 커밋 메시지: `<타입>(<파트>): <요약>`. 예: `feat(web): QR 접속 화면 추가`
- `.env`, 비밀키, 모델 가중치는 커밋하지 않는다.
- 전체 규칙: `docs/git-rules.html`
