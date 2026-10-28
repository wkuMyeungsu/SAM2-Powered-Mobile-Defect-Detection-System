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
4. **누적 집계**: 과자 중심이 검사선(화면 폭 34%)을 지나는 순간에만 센다. 같은 과자가 여러 프레임에 찍혀도 한 번만 센다
5. **보내기**: 마킹된 JPEG + 판정 요약을 gateway 로 (`POST http://gateway:8001/result`)

입출력 형식 제안 (확인 후 `docs/contracts/` 로 옮길 예정):
- [contracts/inference-visualizer.md](contracts/inference-visualizer.md): inference → visualizer
- [contracts/visualizer-gateway.md](contracts/visualizer-gateway.md): visualizer → gateway → web

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| POST | `/visualize` | inference 결과 받기 |
| POST | `/reset` | 누적 수, frame_id 기록 초기화 |
| GET | `/latest.jpg` | 마지막으로 그린 프레임 (브라우저로 바로 확인) |
| GET | `/health` | 상태와 누적 수 |
| GET | `/docs` | FastAPI 자동 문서 |

## 실행

저장소 루트에서:

```bash
docker compose up --build visualizer
```

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
| `SAME_LINE_TOL` | `0.05` | 같은 과자로 볼 y 차이 한계 (0~1) |
| `RESET_GAP` | `300` | frame_id 가 이만큼 작아지면 gateway 재시작으로 보고 자동 초기화 |

## 알아둘 점

- **visualizer 는 1개만 띄운다.** 누적 수와 프레임 순서를 메모리에 들고 있어서 여러 개로 복제하면 집계가 깨진다.
- 화면 글자는 영어만 쓴다. OpenCV 기본 글꼴은 한글을 그리지 못한다.
- 누적 집계는 과자가 **오른쪽 → 왼쪽**으로 움직인다고 가정한다 (시뮬레이터 기준).
- 누적 수는 메모리에만 있어서 visualizer 를 재시작하면 0 이 된다.
