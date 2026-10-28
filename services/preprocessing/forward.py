"""전처리 결과를 inference 로 넘기는 백그라운드 전송기.

gateway 는 기다리지 않게 바로 응답하고, 전송은 스레드 하나가 한 번에 한 프레임씩 한다.
inference 가 처리 중인 동안 들어온 프레임은 가장 최신 것 하나만 남기고 버린다.
실시간 화면에서는 밀린 옛 프레임보다 지금 프레임이 중요하기 때문이다.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("preprocessing")


class Forwarder:
    def __init__(self, url: str, timeout: float):
        self.url = url
        self.timeout = timeout
        self._cond = threading.Condition()
        self._pending = None  # 아직 보내지 않은 가장 최신 프레임 하나
        self.stats = {
            "received": 0,   # 전송 대기열에 들어온 프레임
            "replaced": 0,   # 보내기 전에 더 새 프레임이 와서 버린 프레임
            "sent": 0,       # inference 가 200 으로 받은 프레임
            "busy": 0,       # inference 가 503 (다른 프레임 처리 중) 으로 거절한 프레임
            "errors": 0,     # 연결 실패, 시간 초과, 그 밖의 오류
            "last_ms": None,  # 마지막 전송 왕복 시간
        }
        if url:
            threading.Thread(target=self._loop, name="forwarder", daemon=True).start()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def submit(self, payload: dict) -> str:
        """프레임을 대기열에 넣는다. 반환값: queued / replaced / disabled."""
        if not self.url:
            return "disabled"
        with self._cond:
            self.stats["received"] += 1
            status = "queued"
            if self._pending is not None:
                self.stats["replaced"] += 1
                status = "replaced"  # 이전 대기 프레임을 이 프레임으로 바꿨다
            self._pending = payload
            self._cond.notify()
        return status

    def _loop(self):
        while True:
            with self._cond:
                while self._pending is None:
                    self._cond.wait()
                payload, self._pending = self._pending, None
            self._send(payload)

    def _send(self, payload: dict) -> str:
        body = json.dumps(payload).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read()
            result = "sent"
        except urllib.error.HTTPError as e:
            if e.code == 503:
                result = "busy"  # inference 규칙: 기다리지 말고 이 프레임은 버린다
            else:
                result = "errors"
                log.warning("inference 오류 %s (frame %s): %s", e.code, payload.get("frame_id"),
                            e.read()[:200].decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            result = "errors"
            log.warning("inference 전송 실패 (frame %s): %s", payload.get("frame_id"), e)
        with self._cond:
            self.stats[result] += 1
            self.stats["last_ms"] = round((time.perf_counter() - start) * 1000, 1)
        return result

    def snapshot(self) -> dict:
        with self._cond:
            return dict(self.stats, pending=self._pending is not None)
