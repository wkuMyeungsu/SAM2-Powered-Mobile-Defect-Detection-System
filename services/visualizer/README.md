# visualizer (C4 영상 재수신, port 8004)

inference 가 보낸 판정 결과를 원본 프레임 위에 그리고, gateway 로 돌려보낸다.

```
휴대폰 → gateway(8001) → preprocessing(8002) → inference(8003) → [visualizer(8004)] → gateway(8001) → web(8000)
```

## 하는 일

1. **받기**: inference 로부터 원본 프레임 + 과자 윤곽선 + 원형도 + PASS/FAIL (`POST /visualize`)
2. **그리기**
   - FAIL: 빨간 윤곽선 + 반투명 빨간 채우기
   - PASS: 초록 윤곽선
   - WAIT: 회색 윤곽선. 화면 테두리에 걸쳐 잘려 보이는 과자는 원형도가 낮게 나와 오판되므로 판정을 보류한다
   - 과자 위에 `#12 FAIL CUT` 처럼 번호·판정·판정 이유를 쓴다. 원형도만 쓰면 원형도가 같은데 PASS·FAIL 이 갈리는 것처럼 보이기 때문이다
     (`CUT` 잘림·깨짐, `NOTCH` 파임·조각 없음, `SHAPE` 둥글지 않음, `EDGE` 화면 끝에 걸림, `UNSURE` 마스크 점수 낮음. inference 가 이유를 안 보내면 원형도)
   - `MARK_DETAIL=1` 이면 불량 아래에 판정을 가른 값(`fill 0.949`)을 한 줄 더 쓴다
   - 화면 절반보다 큰 윤곽선(모니터·벽 같은 배경)은 그리지 않고, 과자가 하나도 없으면 화면 아래에 촬영 안내(`NO COOKIES FOUND - move closer`)를 띄운다
   - 검사선과 좌상단 상태 표시 (프레임 번호, 지연시간을 앞 단계·visualizer 로 나눈 값, 화면 내 과자 수, 누적 수)
3. **순서 정리**: inference 를 여러 대 띄우면 결과가 순서 없이 도착한다. 이미 보낸 것보다 오래된 프레임은 버린다
4. **누적 집계**: 과자 중심이 검사선(화면 폭 34%)을 지나는 순간에만 센다. 과자를 하나씩 '가장 가까운 과자'와 짝짓지 않고 벨트 전체의 이동량과 속도를 추정해 짝지으므로, SAM 2 가 느려(0.5fps) 과자가 많이 움직여도 정확히 센다. 과자는 오른쪽 → 왼쪽으로 움직인다고 가정한다. 이전 프레임의 과자와 짝을 지어 "센 과자" 표시를 이어받으므로, 같은 과자가 여러 프레임에 찍히거나 마스크가 흔들려 검사선을 넘나들어도 한 번만 센다
5. **보내기**: 마킹된 JPEG + 판정 요약을 gateway 로 (`POST http://gateway:8001/result`). inference 에는 바로 응답하고 전송은 대기열에서 순서대로 한다. gateway 가 느리거나 꺼져 있어도 inference 를 기다리게 하지 않는다
6. **성능 측정**: 파이프라인 끝에서 처리량(fps)과 지연시간을 잰다 (`GET /metrics`). Scale-out 전후 비교에 쓴다

입출력 형식 제안 (확인 후 `docs/contracts/` 로 옮길 예정):
- [contracts/inference-visualizer.md](contracts/inference-visualizer.md): inference → visualizer
- [contracts/visualizer-gateway.md](contracts/visualizer-gateway.md): visualizer → gateway → web

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/visualize` | inference 결과 받기 |
| POST | `/reset` | 누적 수, frame_id 기록, 측정값 초기화 |
| GET | `/metrics` | 최근 10초 처리량(fps), 지연시간을 앞 단계·visualizer 로 나눈 값(평균·p95·최대), 받음·처리·버림·전송 횟수 |
| GET | `/latest.jpg` | 마지막으로 그린 프레임 (브라우저로 바로 확인) |
| GET | `/health` | 상태와 누적 수 |
| GET | `/docs` | FastAPI 자동 문서 |

## 실행

저장소 루트에서:

```bash
docker compose up --build visualizer
docker compose run --rm visualizer python -m unittest -v test_visualizer   # 단위 테스트
```

## 받는 형식이 달라도 연결되게 한 부분

inference 가 preprocessing 결과 형식을 그대로 따라와도 끊기지 않게 받는다.

| 들어오는 값 | 처리 |
|---|---|
| `frame_id` 가 `"123"` 문자열 | 숫자로 바꿔 순서 정리. 숫자가 아니면 도착 순서대로 처리 |
| `timestamp` 없음 | 지연시간만 `null` |
| `contour` 가 픽셀 좌표 (값 > 2) | `width`/`height`(없으면 이미지 크기)로 나눠 0~1 로 변환 |
| 오류 | preprocessing 과 같은 `400 {"error": "..."}` |

## 혼자 테스트하기 (inference·gateway 없이)

테스트 도구는 컨테이너에 들어가지 않는다. `tests/` 의 스크립트를 내 PC 에서 실행해 컨테이너로 보낸다.

1. 저장소 루트 `.env` 에 한 줄 추가 (visualizer 가 내 PC 의 가짜 gateway 로 보내도록. `.env` 는 커밋하지 않는다)
   ```
   GATEWAY_URL=http://host.docker.internal:8001/result
   ```
2. 터미널 3개:
   ```bash
   pip install -r services/visualizer/requirements-dev.txt
   python services/visualizer/tests/mock_gateway.py          # 1) 가짜 gateway (8001), 받은 프레임을 tests/out/ 에 저장
   docker compose up --build visualizer                      # 2) visualizer
   python services/visualizer/tests/send_sample.py           # 3) 가짜 inference 결과 60프레임 전송
   python services/visualizer/tests/send_sample.py --shuffle #    순서 섞어서 (inference 여러 대 상황)
   ```

`send_sample.py` 는 끝에 정답 개수를 출력한다. gateway 터미널의 누적 수와 같으면 정상이다.

### 컨베이어 시뮬레이터로 테스트

`capture_sim.py` 가 시뮬레이터(`conveyor-cookie-sim.html`)를 Edge 로 자동 조작해서 프레임 PNG + 정답 라벨 JSON 을 연속 저장한다 (초당 5장 샘플링과 같은 간격).

```bash
python services/visualizer/tests/capture_sim.py --html 경로/conveyor-cookie-sim.html --frames 40
python services/visualizer/tests/send_sample.py --sim-dir services/visualizer/tests/sim_frames
```

끝에 시뮬레이터가 센 정답 개수가 출력된다. 라벨 정답을 판정으로 그대로 쓰므로 inference 가 완벽하다고 가정한 테스트다.
40프레임 테스트에서 visualizer 집계는 정답과 일치했다 (통과 14개, 불량 3개).

## 화면이 이상할 때: 어느 단계 문제인지 진단

휴대폰 프레임 1장을 preprocessing → inference → visualizer 에 차례로 넣고 단계마다 그림을 저장한다.
`docker compose up` 으로 서비스를 띄운 상태에서, **휴대폰 촬영은 멈추고** 실행한다.

```bash
pip install httpx opencv-python-headless numpy
python services/visualizer/tests/diagnose.py 휴대폰프레임.jpg
```

`services/visualizer/tests/diagnose_out/compare.jpg` 에 세 그림이 나란히 저장된다.

| 그림 | 내용 | 이상하면 |
|---|---|---|
| 1 preprocessing | 과자 후보 박스. 화면의 5%보다 큰 박스는 빨간색 | 과자에 박스가 없음 → preprocessing |
| 2 inference (direct) | inference 가 돌려준 윤곽선을 진단 도구가 직접 그림 | 박스는 맞는데 윤곽선이 틀림 → inference |
| 3 visualizer | visualizer 가 그린 그림 | 2 와 위치·모양이 다름 → visualizer |

휴대폰 프레임은 gateway 점검 화면(http://localhost:8001)의 원본 미리보기를 저장하거나, 휴대폰으로 직접 찍은 사진을 쓴다.
모니터 전체·벽·책상이 함께 찍히면 배경이 큰 후보로 잡혀 과자를 못 찾는다. 휴대폰을 가로로 들고 컨베이어만 화면에 꽉 차게 찍는다.

## 설정 (환경변수)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PORT` | `8004` | docker-compose 가 넣어 준다 |
| `GATEWAY_URL` | `http://gateway:8001/result` | 결과 보낼 주소 |
| `JPEG_QUALITY` | `80` | 마킹 프레임 화질 |
| `SCAN_X` | `0.34` | 검사선 x 위치 (0~1). 시뮬레이터와 같은 값 |
| `EDGE_MARGIN` | `0.005` | 테두리에서 이 거리 안에 닿으면 WAIT |
| `MAX_JUMP` | `0.6` | 처리한 두 프레임 사이 벨트가 움직일 수 있는 최대 거리 (0~1). SAM 2 가 느리면 크다 |
| `TRACK_HISTORY` | `6` | 벨트 속도를 맞춰 볼 최근 프레임 수 |
| `JITTER` | `0.03` | 마스크가 흔들려 과자 중심이 뒤로 갈 수 있는 거리 (0~1) |
| `SAME_LINE_TOL` | `0.05` | 같은 과자로 볼 y 차이 한계 (0~1) |
| `RESET_GAP` | `300` | frame_id 가 이만큼 작아지면 gateway 재시작으로 보고 자동 초기화 |
| `SEND_QUEUE` | `3` | gateway 전송 대기열 크기. 넘치면 가장 오래된 프레임부터 버린다 |
| `MAX_BODY_BYTES` | `10485760` | 요청 본문 최대 크기 (preprocessing 과 같음) |
| `METRICS_WINDOW` | `10` | `/metrics` 의 fps·지연시간 계산 구간(초) |
| `CORS_ORIGINS` | `*` | `/reset`, `/metrics` 를 브라우저에서 부를 수 있는 출처. web(8000) 대시보드용 |
| `MARK_DETAIL` | `0` | `1` 이면 불량 과자 아래에 판정을 가른 값(`fill 0.949` 등)을 한 줄 더 쓴다. 판정 기준을 맞출 때 쓴다 |
| `MAX_OBJECT_SPAN` | `0.5` | 가로나 세로가 화면의 이 비율보다 큰 윤곽선은 배경으로 보고 무시한다 |

## 성능 측정 (Scale-out 전후 비교)

```bash
curl -X POST http://localhost:8004/reset      # 측정 시작 전 초기화
# ... 영상 흘리기 ...
curl http://localhost:8004/metrics
```

```json
{"window_s": 10.0, "fps": 16.7, "latency_ms": {"avg": 84.1, "p95": 113, "max": 2131},
 "upstream_ms": {"avg": 70.2, "p95": 98, "max": 2110}, "visualizer_ms": {"avg": 13.9, "p95": 21, "max": 40}, "render_ms_avg": 9.5,
 "counts": {"received": 200, "processed": 167, "dropped_old": 33, "sent": 167, "send_errors": 0, "send_skipped": 0}}
```

- `fps`: visualizer 가 끝까지 처리한 초당 프레임 수. 파이프라인 전체 처리량이다
- `latency_ms`: gateway 수신부터 visualizer 처리까지. gateway 가 `timestamp` 를 붙여야 나온다
- `upstream_ms` / `visualizer_ms`: 위 지연을 앞 단계(preprocessing·inference)와 visualizer 로 나눈 값. 느릴 때 어디가 느린지 본다

마킹 화면 왼쪽 위에도 `DELAY 1450 ms = AI/PRE 1432 + MARK 18` 처럼 나눠서 쓴다 (MARK 는 최근 평균).
- `dropped_old`: 순서가 늦게 도착해 버린 프레임. inference 를 여러 대 띄우면 생긴다

## 처리 속도

프레임 하나를 받아서 디코딩, 마킹, JPEG 압축까지 걸리는 시간 (스레드 1개, 과자 15개, 개발 PC 기준).

| 받는 이미지 크기 | 시간 | 최대 처리량 |
|---|---|---|
| 1024×576 (preprocessing 출력) | 약 18ms | 약 57 fps |
| 1920×1080 (원본) | 약 43ms | 약 23 fps |

요청마다 스레드에서 처리하므로 코어가 여럿이면 더 많이 받는다. 불량 영역 칠하기를 과자 주변 사각형 안에서만 하고 윤곽선 안티앨리어싱을 끈 결과로, 처음보다 약 2배 빠르다.
inference 를 여러 대로 늘렸을 때 visualizer 가 새 병목이 되지 않는지 `/metrics` 의 `fps` 로 확인한다.

## 알아둘 점

- **visualizer 는 1개만 띄운다.** 누적 수와 프레임 순서를 메모리에 들고 있어서 여러 개로 복제하면 집계가 깨진다.
- 화면 글자는 영어만 쓴다. OpenCV 기본 글꼴은 한글을 그리지 못한다.
- 누적 집계는 과자가 **오른쪽 → 왼쪽**으로 움직인다고 가정한다 (시뮬레이터 기준).
- 누적 수는 메모리에만 있어서 visualizer 를 재시작하면 0 이 된다.
