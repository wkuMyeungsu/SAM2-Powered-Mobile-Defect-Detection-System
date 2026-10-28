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
   - 과자 위에 `FAIL 0.62` 처럼 판정과 원형도를 쓴다
   - 검사선과 좌상단 상태 표시 (프레임 번호, 지연시간, 화면 내 과자 수, 누적 수)
3. **순서 정리**: inference 를 여러 대 띄우면 결과가 순서 없이 도착한다. 이미 보낸 것보다 오래된 프레임은 버린다
4. **누적 집계**: 과자 중심이 검사선(화면 폭 34%)을 지나는 순간에만 센다. 이전 프레임의 과자와 짝을 지어 "센 과자" 표시를 이어받으므로, 같은 과자가 여러 프레임에 찍히거나 마스크가 흔들려 검사선을 넘나들어도 한 번만 센다
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
| GET | `/metrics` | 최근 10초 처리량(fps), 지연시간(평균·p95·최대), 그리기 시간, 받음·처리·버림·전송 횟수 |
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
| `contour` 가 픽셀 좌표 (값 > 1) | `width`/`height`(없으면 이미지 크기)로 나눠 0~1 로 변환 |
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

## 설정 (환경변수)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `PORT` | `8004` | docker-compose 가 넣어 준다 |
| `GATEWAY_URL` | `http://gateway:8001/result` | 결과 보낼 주소 |
| `JPEG_QUALITY` | `80` | 마킹 프레임 화질 |
| `SCAN_X` | `0.34` | 검사선 x 위치 (0~1). 시뮬레이터와 같은 값 |
| `EDGE_MARGIN` | `0.005` | 테두리에서 이 거리 안에 닿으면 WAIT |
| `MAX_JUMP` | `0.15` | 두 프레임 사이 과자 최대 이동 거리 (0~1). 프레임을 띄엄띄엄 처리하면 키운다 |
| `JITTER` | `0.03` | 마스크가 흔들려 과자 중심이 뒤로 갈 수 있는 거리 (0~1) |
| `SAME_LINE_TOL` | `0.05` | 같은 과자로 볼 y 차이 한계 (0~1) |
| `RESET_GAP` | `300` | frame_id 가 이만큼 작아지면 gateway 재시작으로 보고 자동 초기화 |
| `SEND_QUEUE` | `3` | gateway 전송 대기열 크기. 넘치면 가장 오래된 프레임부터 버린다 |
| `MAX_BODY_BYTES` | `10485760` | 요청 본문 최대 크기 (preprocessing 과 같음) |
| `METRICS_WINDOW` | `10` | `/metrics` 의 fps·지연시간 계산 구간(초) |

## 성능 측정 (Scale-out 전후 비교)

```bash
curl -X POST http://localhost:8004/reset      # 측정 시작 전 초기화
# ... 영상 흘리기 ...
curl http://localhost:8004/metrics
```

```json
{"window_s": 10.0, "fps": 16.7, "latency_ms": {"avg": 84.1, "p95": 113, "max": 2131}, "render_ms_avg": 50.6,
 "counts": {"received": 200, "processed": 167, "dropped_old": 33, "sent": 167, "send_errors": 0, "send_skipped": 0}}
```

- `fps`: visualizer 가 끝까지 처리한 초당 프레임 수. 파이프라인 전체 처리량이다
- `latency_ms`: gateway 수신부터 visualizer 처리까지. gateway 가 `timestamp` 를 붙여야 나온다
- `dropped_old`: 순서가 늦게 도착해 버린 프레임. inference 를 여러 대 띄우면 생긴다

## 알아둘 점

- **visualizer 는 1개만 띄운다.** 누적 수와 프레임 순서를 메모리에 들고 있어서 여러 개로 복제하면 집계가 깨진다.
- 화면 글자는 영어만 쓴다. OpenCV 기본 글꼴은 한글을 그리지 못한다.
- 누적 집계는 과자가 **오른쪽 → 왼쪽**으로 움직인다고 가정한다 (시뮬레이터 기준).
- 누적 수는 메모리에만 있어서 visualizer 를 재시작하면 0 이 된다.
