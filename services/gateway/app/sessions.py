"""세션: 휴대폰 1대 + 보는 화면 여러 개를 묶는 단위.

휴대폰이 보낸 최신 프레임, 처리 중인 프레임, 처리 현황을 세션마다 들고 있다.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("uvicorn.error")

ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 헷갈리는 글자(i, l, o, 0, 1) 제외
ID_LENGTH = 6
RECENT_FRAMES = 200  # 처리 시간 계산용으로 기억하는 최근 프레임 수


def now_ms() -> int:
    return int(time.time() * 1000)


def data_uri(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """JPEG 머리말에서 (가로, 세로)를 읽는다. JPEG가 아니면 None. 이미지를 디코딩하지 않는다."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    i = 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker == 0xFF:  # 채움 바이트
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2:i + 4], "big")
        if marker in sof and i + 9 <= len(data):
            height = int.from_bytes(data[i + 5:i + 7], "big")
            width = int.from_bytes(data[i + 7:i + 9], "big")
            return width, height
        if seg_len < 2:
            return None
        i += 2 + seg_len
    return None


@dataclass
class Session:
    id: str
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    phone: WebSocket | None = None
    phone_info: dict[str, Any] | None = None
    viewers: set[WebSocket] = field(default_factory=set)

    latest: bytes | None = None  # 휴대폰이 보낸 가장 최근 프레임
    latest_id: int = 0
    latest_size: tuple[int, int] | None = None
    received_at: "OrderedDict[int, int]" = field(default_factory=OrderedDict)  # frame_id → 받은 시각(ms)
    last_preview_at: float = 0.0
    last_result: dict[str, Any] | None = None  # 새로 들어온 화면에 바로 보여 줄 마지막 결과

    inflight: tuple[int, float] | None = None  # 처리 중인 (frame_id, 보낸 시각)
    new_frame: asyncio.Event = field(default_factory=asyncio.Event)
    slot_free: asyncio.Event = field(default_factory=asyncio.Event)
    forward_task: asyncio.Task | None = None

    frames_received: int = 0
    frames_dropped: int = 0  # JPEG가 아니거나 너무 커서 버린 수
    forwarded: int = 0
    results: int = 0
    forward_errors: int = 0
    forward_timeouts: int = 0
    last_error: str | None = None

    def touch(self) -> None:
        self.last_active = time.time()

    def add_frame(self, jpeg: bytes, size: tuple[int, int]) -> int:
        self.latest_id += 1
        self.latest = jpeg
        self.latest_size = size
        self.frames_received += 1
        self.received_at[self.latest_id] = now_ms()
        while len(self.received_at) > RECENT_FRAMES:
            self.received_at.popitem(last=False)
        self.touch()
        self.new_frame.set()
        return self.latest_id

    def status(self) -> dict[str, Any]:
        return {
            "type": "status",
            "session_id": self.id,
            "phone_connected": self.phone is not None,
            "phone_info": self.phone_info,
            "viewers": len(self.viewers),
            "frames_received": self.frames_received,
            "frames_dropped": self.frames_dropped,
            "forwarded": self.forwarded,
            "results": self.results,
            "forward_errors": self.forward_errors,
            "forward_timeouts": self.forward_timeouts,
            "last_error": self.last_error,
            "inflight_frame_id": self.inflight[0] if self.inflight else None,
        }


class SessionStore:
    def __init__(self, ttl: float, max_sessions: int = 100):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}

    def __len__(self) -> int:
        return len(self._sessions)

    def __iter__(self):
        return iter(list(self._sessions.values()))

    def create(self) -> Session:
        if len(self._sessions) >= self.max_sessions:
            self.prune(force_oldest=True)
        while True:
            sid = "".join(secrets.choice(ID_ALPHABET) for _ in range(ID_LENGTH))
            if sid not in self._sessions:
                break
        session = Session(sid)
        self._sessions[sid] = session
        return session

    def get(self, sid: str) -> Session | None:
        return self._sessions.get(sid)

    def prune(self, force_oldest: bool = False) -> list[Session]:
        """연결이 하나도 없고 오래된 세션을 지운다. 지운 세션을 돌려준다 (작업 정리용)."""
        now = time.time()
        idle = [s for s in self._sessions.values() if s.phone is None and not s.viewers]
        removed = [s for s in idle if now - s.last_active > self.ttl]
        if force_oldest and not removed and idle:
            removed = [min(idle, key=lambda s: s.last_active)]
        for s in removed:
            self._sessions.pop(s.id, None)
        return removed


async def broadcast(session: Session, payload: dict[str, Any]) -> int:
    """보는 화면 모두에 JSON을 보낸다. 2초 안에 못 받는 화면은 연결을 정리한다. 보낸 화면 수를 돌려준다."""
    if not session.viewers:
        return 0
    text = json.dumps(payload, ensure_ascii=False)
    delivered = 0
    for ws in list(session.viewers):
        try:
            await asyncio.wait_for(ws.send_text(text), timeout=2.0)
            delivered += 1
        except Exception:
            session.viewers.discard(ws)
    return delivered


async def deliver_result(
    session: Session,
    frame_id: int,
    image: bytes,
    result: dict[str, Any],
    stage: str = "final",
) -> int:
    """처리 결과를 화면에 보내고, 그 프레임이 처리 중이었다면 다음 프레임을 보낼 수 있게 자리를 비운다.

    stage: final(visualizer가 보낸 최종 결과) · preprocessing(전처리 응답) · echo(테스트)
    """
    received = session.received_at.get(frame_id)
    payload = {
        "type": "result",
        "stage": stage,
        "frame_id": frame_id,
        "latency_ms": now_ms() - received if received else None,
        "result": result,
        "image": data_uri(image),
    }
    if session.inflight and session.inflight[0] == frame_id:
        session.inflight = None
        session.slot_free.set()
    session.results += 1
    session.last_result = payload
    session.touch()
    return await broadcast(session, payload)
