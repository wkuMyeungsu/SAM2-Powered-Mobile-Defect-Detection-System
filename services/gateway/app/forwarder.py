"""휴대폰 프레임을 preprocessing으로 보내는 일 (팀 구조도 ②).

preprocessing POST /frame이 전처리 뒤 inference로 넘기고(③), inference → visualizer → gateway POST /result로
결과가 돌아온다(④⑤). gateway는 /frame의 202 응답(보통 수십 ms)만 기다린다.
형식: services/preprocessing/contracts/gateway-preprocessing.md

세션마다 한 번에 1장만 보낸다. 응답을 기다리는 동안 휴대폰이 보낸 프레임은 가장 최신 1장만 남는다.
inference가 느린 것은 preprocessing이 최신 프레임만 남겨 처리한다.

GATEWAY_FORWARD_URL을 /preprocess로 바꾸면(확인용) 전처리 결과를 점검 화면에 보여 준다.
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from .config import Settings
from .sessions import Session, SessionStore, b64, now_ms, relay_result, send_stage_result

log = logging.getLogger("uvicorn.error")

RETRY_DELAY = 2.0  # 보내기에 실패하면 이만큼 쉬고 다시 보낸다
LOG_EVERY = 30.0  # 같은 오류는 이 간격으로만 로그를 남긴다


class StageError(Exception):
    """처리 단계 호출 실패."""


class Forwarder:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, store: SessionStore):
        self.settings = settings
        self.client = client
        self.store = store
        self._last_log: dict[str, float] = {}

    def start(self, session: Session) -> None:
        if self.settings.forward_mode == "off":
            return
        if session.forward_task is None or session.forward_task.done():
            session.forward_task = asyncio.create_task(self._run(session), name=f"forward-{session.id}")

    @staticmethod
    async def stop(session: Session) -> None:
        task, session.forward_task = session.forward_task, None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        session.inflight = None

    async def _run(self, session: Session) -> None:
        last_sent = 0.0
        while True:
            await session.new_frame.wait()
            gap = last_sent + self.settings.forward_interval - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)
            session.new_frame.clear()
            frame, frame_id = session.latest, session.latest_id
            if frame is None:
                continue
            session.inflight = frame_id
            last_sent = time.monotonic()
            failed = False
            try:
                await self._process(session, frame_id, frame)
                session.forwarded += 1
            except StageError as e:
                self._record_error(session, str(e))
                failed = True
            finally:
                session.inflight = None
            if failed:
                await asyncio.sleep(RETRY_DELAY)

    async def _process(self, session: Session, frame_id: int, frame: bytes) -> None:
        _, received = self.store.frame(frame_id)
        timestamp = (received or now_ms()) / 1000  # gateway가 받은 시각 (unix 초). visualizer가 지연시간을 잰다

        if self.settings.forward_mode == "echo":
            # 처리 단계 없이 받은 프레임을 visualizer 결과 형식으로 바로 돌려준다 (web 화면 개발·테스트용)
            await relay_result(self.store, {
                "frame_id": frame_id, "timestamp": timestamp, "latency_ms": None, "image": b64(frame),
                "summary": {"in_frame": 0, "defect_in_frame": 0, "passed_total": 0, "defect_total": 0},
                "objects": [],
            }, stage="echo")
            return

        url = self.settings.forward_url
        headers = {"Content-Type": "image/jpeg", "X-Frame-Id": str(frame_id), "X-Timestamp": f"{timestamp:.3f}", "X-Session-Id": session.id}
        try:
            r = await self.client.post(url, content=frame, headers=headers, timeout=self.settings.forward_timeout)
        except httpx.HTTPError as e:
            raise StageError(f"preprocessing 연결 실패 ({type(e).__name__})") from e
        if not r.is_success:
            raise StageError(f"preprocessing 응답 {r.status_code}: {r.text[:120]}")
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}

        status = body.get("status")
        if status:
            session.stage_status = status
            if status == "replaced":
                session.replaced += 1
            elif status == "disabled":
                self._record_error(session, "preprocessing이 inference로 보내지 않음 (preprocessing의 INFERENCE_URL이 비어 있음)", count=False)
        if isinstance(body.get("image"), str):
            # /preprocess(확인용) 응답: 전처리 결과를 점검 화면에 보여 준다
            await send_stage_result(session, frame_id, body["image"], {k: v for k, v in body.items() if k not in ("image", "original_image")}, "preprocessing")

    def _record_error(self, session: Session, message: str, count: bool = True) -> None:
        if count:
            session.forward_errors += 1
        session.last_error = message
        now = time.monotonic()
        if now - self._last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[message] = now
            log.warning("[%s] %s (같은 내용은 %d초마다 기록)", session.id, message, int(LOG_EVERY))
