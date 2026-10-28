# 재수신: visualizer → gateway → web

상태: **확인됨**. gateway(우진)가 그대로 받고 web으로 넘깁니다 (`docs/contracts/result-return.md`). web(명수) 대시보드도 이 형식을 씁니다. gateway → 전처리 → 추론 → visualizer → gateway → `/ws/view` 전체 연결 테스트 통과.

## 1. visualizer → gateway

`POST http://gateway:8001/result`, `Content-Type: application/json`

```json
{
  "frame_id": 123,
  "timestamp": 1761600000.123,
  "latency_ms": 184,
  "image": "<마킹된 프레임 JPEG base64>",
  "summary": {
    "in_frame": 8,
    "defect_in_frame": 2,
    "passed_total": 41,
    "defect_total": 12
  },
  "objects": [
    { "id": 17, "result": "FAIL", "circularity": 0.83, "reason": "straight cut or broken" },
    { "id": 18, "result": "PASS", "circularity": 0.90, "reason": "" },
    { "id": 21, "result": "WAIT", "circularity": 0.55, "reason": "touches frame edge" }
  ]
}
```

| 필드 | 설명 |
|---|---|
| `frame_id`, `timestamp` | gateway가 처음 붙인 값을 받은 타입 그대로 돌려드립니다 (`frame_id`는 숫자 또는 문자열). 중간에 `timestamp`가 빠지면 `null`입니다. |
| `latency_ms` | gateway 수신부터 visualizer 처리 완료까지 걸린 시간입니다. 성능 측정에 쓰실 수 있습니다. `timestamp`가 없으면 `null`입니다. |
| `image` | 윤곽선과 판정 글자를 그린 JPEG(화질 80) base64입니다. 이미지와 판정이 어긋나지 않도록 한 메시지에 함께 담았습니다. |
| `summary.in_frame` | 지금 화면에 보이는 과자 수입니다(WAIT 포함). |
| `summary.defect_in_frame` | 그중 FAIL 수입니다. |
| `summary.passed_total` | 검사선을 지난 과자의 누적 수입니다. 같은 과자를 여러 번 세지 않습니다. |
| `summary.defect_total` | 그중 FAIL 누적 수입니다. |
| `objects[].reason` | inference가 붙인 판정 이유입니다. inference가 보내지 않으면 `null`입니다. |
| `objects[].id` | 과자 고정 번호입니다. 프레임이 바뀌어도 같은 과자는 같은 번호이고, 마킹 이미지에도 `#17`처럼 같은 번호가 표시됩니다. |
| `objects[].result` | `PASS` / `FAIL` / `WAIT`(화면 테두리에 걸쳐 판정 보류)입니다. |

프레임 순서는 visualizer에서 정리해서 보내드립니다. gateway에는 오래된 프레임이 가지 않습니다.

## 2. gateway → web

브라우저는 서버가 먼저 보내는 데이터를 HTTP로 받을 수 없어서 **WebSocket** 사용을 제안드립니다.

- 제안 주소: `ws://<서버 IP>:8001/ws/view`
- 제안 내용: 위 JSON을 **그대로** 텍스트 메시지로 전달
- web에서 표시하는 예시입니다.
  ```js
  const ws = new WebSocket(`ws://${location.hostname}:8001/ws/view`);
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    img.src = "data:image/jpeg;base64," + m.image;
    total.textContent = m.summary.passed_total;
    defect.textContent = m.summary.defect_total;
  };
  ```

## 확인 부탁드리는 점

1. **gateway**: `/result` 경로로 받아 주실 수 있을까요?
2. **gateway**: 지연시간을 재려면 프레임을 받을 때 `frame_id`와 함께 `timestamp`(unix 초)를 붙여서, preprocessing·inference를 거쳐 visualizer까지 그대로 전달되게 해 주세요. 지금 preprocessing 응답에는 `timestamp`가 없어서, preprocessing·inference 담당자께도 같이 요청이 필요합니다.
3. **gateway**: web으로 JSON을 그대로 넘겨 주실지, 형식을 바꾸실지 알려 주세요.
4. **gateway·web**: WebSocket 주소를 정해 주세요.
5. **web**: 시연을 다시 시작할 때 누적 수를 0으로 만들려면 `POST http://visualizer:8004/reset`을 호출해 주세요. "초기화" 버튼에 연결해 주시면 좋겠습니다. visualizer에 CORS를 열어 두어서 `mode: "no-cors"` 없이 호출하고 응답 코드를 확인하실 수 있습니다. 같은 방식으로 `GET http://<호스트>:8004/metrics`(처리량·지연시간)도 대시보드에서 가져가실 수 있습니다.
