# 주머니 속의 스마트 팩토리

SAM 2 및 모바일 스트리밍 기반 실시간 불량 과자 검출 시스템.
스마트폰 카메라가 보낸 프레임을 4단계 컨테이너가 처리하고, 웹 대시보드에 불량 판정 결과를 표시한다.

## 구조 · 담당 · 포트

| 폴더 | 역할 | 담당 | 호스트 포트 | 컨테이너 주소 |
|---|---|---|---|---|
| `web/` | 홈페이지·대시보드 | 명수 | 8000 | `http://web:8000` |
| `services/gateway/` | QR, 영상 프레임 전달 (C1) | 우진 | 8001 | `http://gateway:8001` |
| `services/preprocessing/` | 전처리 (C2) | 진아 | 8002 | `http://preprocessing:8002` |
| `services/inference/` | SAM 2 추론 (C3) | 대희 | 8003 | `http://inference:8003` |
| `services/visualizer/` | 영상 재수신 (C4) | 도은 | 8004 | `http://visualizer:8004` |

`docs/contracts/`에는 파트 사이에 주고받는 데이터 형식을 적는다.

## 실행

필요한 것: Docker Desktop (실행 중일 것), Docker Compose 2.24 이상.

```bash
docker compose up --build              # 전체
docker compose up --build web          # 내 서비스만
docker compose down                    # 종료
docker compose ps                      # 상태 (healthy 확인)
```

- 현재 `services/*`는 `/health`만 답하는 뼈대 서버다. 담당자가 자기 폴더의 `main.py`와 `Dockerfile`을 바꿔 가며 채운다.
- 호스트 포트가 다른 프로그램과 겹치면 `.env.example`을 `.env`로 복사해 포트만 바꾼다. `.env`가 없어도 기본값으로 실행된다.
- 컨테이너 안에서 다른 서비스는 `localhost`가 아니라 서비스 이름으로 부른다 (`http://inference:8003`).

## 브랜치

- `main`: 최종본. `dev`: 통합. 둘 다 직접 push하지 않고 PR로만 합친다.
- 작업 브랜치: `<태그>/<파트>/<기능>` — 태그는 `feature` `fix` `docs` `refactor` `chore`, 파트는 `web` `gateway` `preprocessing` `inference` `visualizer`.
  예: `feature/web/qr-page`, `fix/inference/empty-mask`
- 작업 브랜치는 `dev`에서 만들고 `dev`로 PR을 올린다. 병합 후 삭제한다.
- 커밋 메시지: `<타입>(<파트>): <요약>`. 예: `feat(web): QR 접속 화면 추가`
- PR 병합은 Create a merge commit만 사용한다.
- `.env`, 비밀키, 모델 가중치(`*.pt` `*.onnx`)는 커밋하지 않는다.
- 전체 절차는 `docs/git-rules.html`.
