"""휴대폰 프레임을 처리 단계로 보내는 일 (팀 구조도 ②③).

프레임 1장마다 차례로:
  1. preprocessing  POST /preprocess  (JPEG 본문) → 전처리 이미지·후보 영역 JSON
  2. inference      POST /infer       (1의 JSON + frame_id·timestamp·원본 이미지)
     inference가 visualizer로 보내고, visualizer가 gateway POST /result로 돌려준다 (main.py).

세션마다 한 번에 1장만 처리한다. 처리하는 동안 휴대폰이 보낸 프레임은 가장 최신 1장만 남고,
처리가 끝나면 그 1장을 보낸다. 그래서 SAM 2가 느려도 결과가 밀리지 않는다.
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

        pre = await self._preprocess(session, frame_id, frame, timestamp)
        if not self.settings.inference_url:
            # inference를 쓰지 않으면 전처리 결과를 점검 화면에 보여 준다
            await send_stage_result(session, frame_id, pre["image"], {k: v for k, v in pre.items() if k != "image"}, "preprocessing")
            return
        try:
            await self._infer(session, frame_id, frame, timestamp, pre)
        except StageError:
            # inference가 없거나 실패해도 전처리 결과는 점검 화면에 보여 준다
            await send_stage_result(session, frame_id, pre["image"], {k: v for k, v in pre.items() if k != "image"}, "preprocessing")
            raise

    async def _preprocess(self, session: Session, frame_id: int, frame: bytes, timestamp: float) -> dict:
        url = self.settings.forward_url
        headers = {"Content-Type": "image/jpeg", "X-Frame-Id": str(frame_id), "X-Session-Id": session.id, "X-Timestamp": f"{timestamp:.3f}"}
        try:
            r = await self.client.post(url, content=frame, headers=headers, timeout=self.settings.forward_timeout)
        except httpx.HTTPError as e:
            raise StageError(f"preprocessing 연결 실패 ({type(e).__name__})") from e
        if r.status_code != 200:
            raise StageError(f"preprocessing 응답 {r.status_code}: {r.text[:120]}")
        try:
            body = r.json()
            if not isinstance(body.get("image"), str):
                raise ValueError("image 없음")
        except ValueError as e:
            raise StageError(f"preprocessing 응답 형식 오류: {e}") from e
        return body

    async def _infer(self, session: Session, frame_id: int, frame: bytes, timestamp: float, pre: dict) -> None:
        """inference /infer는 preprocessing 응답을 그대로 받는다. frame_id·timestamp·원본 이미지를 덧붙인다."""
        body = {**pre, "frame_id": str(frame_id), "timestamp": timestamp, "original_image": b64(frame)}
        try:
            r = await self.client.post(self.settings.inference_url, json=body, timeout=self.settings.inference_timeout)
        except httpx.HTTPError as e:
            raise StageError(f"inference 연결 실패 ({type(e).__name__})") from e
        if r.status_code == 503:  # 이전 프레임을 처리 중: 이 프레임은 버리고 다음 최신 프레임을 보낸다
            session.inference_busy += 1
            return
        if not r.is_success:
            raise StageError(f"inference 응답 {r.status_code}: {r.text[:120]}")

    def _record_error(self, session: Session, message: str) -> None:
        session.forward_errors += 1
        session.last_error = message
        now = time.monotonic()
        if now - self._last_log.get(message, -LOG_EVERY) >= LOG_EVERY:
            self._last_log[message] = now
            log.warning("[%s] 프레임 처리 실패: %s (같은 오류는 %d초마다 기록)", session.id, message, int(LOG_EVERY))
