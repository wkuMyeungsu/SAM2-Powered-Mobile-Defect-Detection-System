# web — 대시보드

- 담당: 명수
- 주소: `http://localhost:8000`

`index.html` 한 장을 nginx가 제공한다. 브라우저가 접속한 주소의 호스트 이름으로 gateway(8001)와 visualizer(8004)에 직접 붙는다.

| 화면 | 쓰는 주소 |
|---|---|
| 검사 화면, 집계, 현재 프레임 판정 | gateway `ws://<호스트>:8001/ws/view` |
| 휴대폰 접속 QR, 연결 상태 | gateway `/qr.svg`, `/api/default-session` |
| 성능 (최근 10초 fps·지연) | visualizer `/metrics` |
| 누적 초기화 버튼 | visualizer `POST /reset` |
| gateway가 없을 때 검사 화면·집계 | visualizer `/latest.jpg`, `/health` |

```bash
docker compose up --build web
```
