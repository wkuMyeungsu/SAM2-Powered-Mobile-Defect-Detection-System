# 전처리 입력: gateway → preprocessing (구조도 ②)

- 상태: **초안**. 진아 님이 구현한 `POST /preprocess` 형식을 그대로 따른다.
- 확인: 우진(gateway) · 진아(preprocessing)

## 요청

gateway가 프레임 1장씩 보낸다. 본문은 휴대폰이 보낸 JPEG 그대로다 (다시 압축하지 않음).

```
POST http://preprocessing:8002/preprocess
Content-Type: image/jpeg
X-Frame-Id: 42
X-Session-Id: a8f3k2
X-Timestamp: 1761620000.123

<JPEG 바이트>
```

| 헤더 | 뜻 |
|---|---|
| `X-Frame-Id` | gateway 전체에서 1부터 늘어나는 번호. preprocessing이 응답의 `frame_id`(문자열)로 돌려준다 |
| `X-Session-Id` | 어느 휴대폰의 프레임인지 (preprocessing은 쓰지 않아도 된다) |
| `X-Timestamp` | gateway가 받은 시각 (unix 초). gateway가 inference에 직접 넘기므로 preprocessing은 쓰지 않아도 된다 |

## 응답

`200` JSON (`services/preprocessing/README.md`): `frame_id`, `image`(전처리 이미지 base64), `width`, `height`, `original_width`, `original_height`, `scale`, `candidates`, `elapsed_ms`.

gateway는 이 응답을 그대로 inference에 넘긴다 (`inference-input.md`). `200`이 아니거나 `image`가 없으면 오류로 기록하고 2초 뒤 최신 프레임으로 다시 보낸다.

## 흐름 제어 (최신 프레임 우선)

gateway는 세션마다 한 번에 1장만 처리한다 (전처리 → 추론 응답까지). 처리하는 동안 휴대폰이 보낸 프레임은 가장 최신 1장만 남기고, 끝나면 그 1장을 보낸다. 그래서 preprocessing은 대기열을 따로 둘 필요가 없다. 보내는 최소 간격은 `GATEWAY_FORWARD_INTERVAL`(기본 0.2초)이다.
