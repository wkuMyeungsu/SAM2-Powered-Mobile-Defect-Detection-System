"""휴대폰 프레임을 처리 단계(preprocessing)로 보내는 일.

세션마다 처리 중인 프레임을 1장만 둔다. 결과가 돌아오거나 시간이 지나면,
그 사이 쌓인 프레임 중 가장 최신 1장만 보낸다 (docs/contracts/preprocessing-input.md).

preprocessing(POST /preprocess)은 JPEG 본문을 받아 전처리 결과를 바로 응답한다.
- 응답이 200 JSON(이미지 포함)이면: 그 결과를 화면에 바로 보내고 다음 프레임으로 넘어간다.
- 응답이 202처럼 본문이 없으면: 뒤 단계(visualizer)가 POST /results로 돌려줄 때까지 기다린다.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time

import httpx

from .config import Settings
from .sessions import Session, deliver_result, now_ms

log = logging.getLogger("uvicorn.error")

RETRY_DELAY = 2.0  # 보내기에 실패하면 이만큼 쉬고 다시 보낸다
LOG_EVERY = 30.0  # 같은 오류는 이 간격으로만 로그를 남긴다


class Forwarder:
    def __init__(self, settings: Settings, client: httpx.AsyncClient):
        self.settings = settings
        self.client = client
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

    async def _wait_slot(self, session: Session) -> None:
        """처리 중인 프레임의 결과가 오거나 제한 시간이 지날 때까지 기다린다."""
        while session.inflight is not None:
            frame_id, sent_at = session.inflight
            remaining = sent_at + self.settings.forward_timeout - time.monotonic()
            if remaining <= 0:
                session.inflight = None
                session.forward_timeouts += 1
                return
            session.slot_free.clear()
            if session.inflight is None:
                return
            try:
                await asyncio.wait_for(session.slot_free.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                pass

    async def _run(self, session: Session) -> None:
        last_sent = 0.0
        while True:
            await session.new_frame.wait()
            await self._wait_slot(session)
            gap = last_sent + self.settings.forward_interval - time.monotonic()
            if gap > 0:
                await asyncio.sleep(gap)

            session.new_frame.clear()
            frame, frame_id = session.latest, session.latest_id
            if frame is None:
                continue
            session.inflight = (frame_id, time.monotonic())
            last_sent = time.monotonic()

            if await self._send(session, frame_id, frame):
                session.forwarded += 1
            else:
                session.inflight = None
                await asyncio.sleep(RETRY_DELAY)

    async def _send(self, session: Session, frame_id: int, frame: bytes) -> bool:
        if self.settings.forward_mode == "echo":
            # 처리 단계 없이 받은 프레임을 그대로 결과로 돌려준다 (web 화면 개발·테스트용)
            asyncio.create_task(deliver_result(session, frame_id, frame, {"verdict": "none", "items": []}, stage="echo"))
            return True

        headers = {
            "Content-Type": "image/jpeg",
            "X-Frame-Id": str(frame_id),
            "X-Session-Id": session.id,
            "X-Received-At": str(session.received_at.get(frame_id, now_ms())),
        }
        try:
            r = await self.client.post(self.settings.forward_url, content=frame, headers=headers, timeout=self.settings.forward_timeout)
        except httpx.HTTPError as e:
            self._record_error(session, f"{self.settings.forward_url} 연결 실패 ({type(e).__name__})")
            return False
        if not r.is_success:
            self._record_error(session, f"{self.settings.forward_url} 응답 {r.status_code}: {r.text[:120]}")
            return False

        stage_result = self._parse_stage_result(r)
        if stage_result is not None:
            image, result = stage_result
            await deliver_result(session, frame_id, image, result, stage="preprocessing")
        return True

    @staticmethod
    def _parse_stage_result(r: httpx.Response) -> tuple[bytes, dict] | None:
        """200 JSON 응답에 base64 이미지가 있으면 (이미지, 나머지 결과)로 나눈다. 아니면 None (콜백을 기다림)."""
        if r.status_code != 200 or "application/json" not in r.headers.get("content-type", ""):
            return None
        try:
            body = r.json()
            image = base64.b64decode(body["image"], validate=True)
        except (ValueError, KeyError, TypeError, binascii.Error):
            return None
        result = {k: v for k, v in body.items() if k != "image"}
        return image, result

    def _record_error(self, session: Session, message: str) -> None:
        session.forward_errors += 1
        session.last_error = message
        now = time.monotonic()
        if now - self._last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[message] = now
            log.warning("[%s] 프레임 전달 실패: %s (같은 오류는 %d초마다 기록)", session.id, message, int(LOG_EVERY))
