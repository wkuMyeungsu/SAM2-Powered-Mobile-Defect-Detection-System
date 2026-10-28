# 휴대폰 없이 서비스 연결 확인

```bash
docker compose --profile test run --rm --build pipeline
```

휴대폰 대신 합성 컨베이어 영상(정상 과자 8개, 불량 4개)을 gateway에 20초 동안 보내고, 대시보드 자리(`/ws/view`)에서 결과를 받아 확인한다. Docker 내부망에서 돌기 때문에 ②~⑥ 구간만 확인한다. 휴대폰·터널·실제 사진은 `docs/phone-checklist.md`로 따로 확인한다.

| 확인 항목 | 통과 기준 |
|---|---|
| health | 4개 서비스 `/health`가 `ok`, web 페이지 응답 |
| 결과 | 대시보드가 결과를 받고, `frame_id`가 오름차순·중복 없음 |
| 판정 | 마킹 이미지가 있고 PASS·FAIL이 모두 나옴 |
| 오류 | gateway·preprocessing·visualizer 전달 오류 0 |
| 누적 집계 | 기대값(통과 6, 불량 2)과 비교해 표시만 함. 어긋나도 실패로 치지 않음 |

- 실패가 하나라도 있으면 종료 코드 1.
- 입력·마킹 이미지는 `tests/pipeline/out/`에 저장된다 (커밋하지 않음).
- visualizer 누적 집계를 초기화하므로 시연 중에는 돌리지 않는다.
