# 파트 간 주고받는 데이터

경계마다 형식을 문서 하나로 적고 담당자 둘이 확인한다. 보내는 쪽이 제안한 문서는 그 서비스 폴더의 `contracts/`에, 여러 파트가 같이 쓰는 문서는 이 폴더에 둔다.

| 번호 | 경계 | 보내는 쪽 → 받는 쪽 | 문서 | 상태 |
|---|---|---|---|---|
| ① | 프레임 입력 | 휴대폰 → gateway | `docs/contracts/frame-input.md` | 초안 |
| ② | 전처리 입력 | gateway → preprocessing | `services/preprocessing/contracts/gateway-preprocessing.md` | 제안 (gateway 확인 필요) |
| ③ | 추론 입력 | preprocessing → inference | `services/preprocessing/contracts/preprocessing-inference.md` | 제안 |
| ④ | 추론 결과 | inference → visualizer | `services/visualizer/contracts/inference-visualizer.md` | 확인됨 |
| ⑤ | 결과 재수신 | visualizer → gateway | `services/visualizer/contracts/visualizer-gateway.md` | 확인됨 |
| ⑥ | 대시보드 전달 | gateway → web | `docs/contracts/result-return.md` | 확인됨 |

상태는 각 문서 첫 부분에 적힌 값이다. ②·③은 코드가 이미 이 형식으로 연결되어 휴대폰 없이 서비스 연결 확인(`docker compose --profile test run --rm --build pipeline`)을 통과하지만, 문서 상태는 아직 바뀌지 않았다.
