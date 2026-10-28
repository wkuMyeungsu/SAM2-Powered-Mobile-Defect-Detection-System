"""세션과 결과 전달.

- 세션: 휴대폰 1대 + 점검 화면 여러 개. 휴대폰이 보낸 최신 프레임과 처리 현황을 들고 있다.
- frame_id: gateway 전체에서 1씩 늘어나는 번호. visualizer는 마지막으로 받은 번호보다 작은 프레임을 버리므로
  (차이가 300 이상일 때만 재시작으로 본다) 세션이 바뀌거나 gateway를 다시 켜도 번호가 작아지면 안 된다.
  그래서 시작값을 시각에서 정한다 (frame_id_start). frame_id로 어느 세션의 프레임인지 찾는다.
- 보는 화면은 두 종류다.
  - web 대시보드 (/ws/view): visualizer가 보낸 결과 JSON을 그대로 받는다 (visualizer-gateway 제안 형식).
  - gateway 점검 화면 (/ws/view/<세션>): status·frame·result 메시지를 받는다.
"""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

ID_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # 헷갈리는 글자(i, l, o, 0, 1) 제외
ID_LENGTH = 6
RECENT_FRAMES = 2000  # frame_id → 세션·받은 시각을 기억하는 수
FRAME_ID_EPOCH = 1_767_225_600  # 2026-01-01 00:00 UTC
FRAME_ID_PER_SECOND = 10  # 초당 이보다 적게 받는 한, 다시 켜도 번호가 이전 실행보다 크다 (휴대폰은 초당 5장)


def now_ms() -> int:
    return int(time.time() * 1000)


def frame_id_start() -> int:
    """gateway를 다시 켜도 이전 실행의 번호보다 크게 시작한다 (2026년부터 0.1초마다 1씩 커지는 값)."""
    return (int(time.time()) - FRAME_ID_EPOCH) * FRAME_ID_PER_SECOND + 1


def data_uri(b64: str) -> str:
    return b64 if b64.startswith("data:") else "data:image/jpeg;base64," + b64


def b64(jpeg: bytes) -> str:
    return base64.b64encode(jpeg).decode("ascii")


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
    viewers: set[WebSocket] = field(default_factory=set)  # 점검 화면

    latest: bytes | None = None  # 휴대폰이 보낸 가장 최근 프레임
    latest_id: int = 0
    last_preview_at: float = 0.0
    last_result: dict[str, Any] | None = None  # 새로 들어온 점검 화면에 바로 보여 줄 마지막 결과

    inflight: int | None = None  # 처리 중인 frame_id
    new_frame: asyncio.Event = field(default_factory=asyncio.Event)
    forward_task: asyncio.Task | None = None

    frames_received: int = 0
    frames_dropped: int = 0  # JPEG가 아니거나 너무 커서 버린 수
    forwarded: int = 0  # 처리 단계로 보낸 수
    inference_busy: int = 0  # inference가 이전 프레임을 처리 중이라 건너뛴 수
    results: int = 0
    forward_errors: int = 0
    last_error: str | None = None

    def touch(self) -> None:
        self.last_active = time.time()

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
            "inference_busy": self.inference_busy,
            "results": self.results,
            "forward_errors": self.forward_errors,
            "last_error": self.last_error,
            "inflight_frame_id": self.inflight,
        }


class SessionStore:
    def __init__(self, ttl: float, max_sessions: int = 100):
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._sessions: dict[str, Session] = {}
        self._frame_ids = itertools.count(frame_id_start())
        self._frames: "OrderedDict[int, tuple[str, int]]" = OrderedDict()  # frame_id → (세션 ID, 받은 시각 ms)
        self.default_id: str | None = None  # /qr.svg가 쓰는 기본 세션
        self.dashboards: set[WebSocket] = set()  # web 대시보드 (/ws/view)
        self.last_dashboard: str | None = None  # 새로 연결한 대시보드에 바로 보낼 마지막 결과

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

    def default(self) -> Session:
        session = self.get(self.default_id) if self.default_id else None
        if session is None:
            session = self.create()
            self.default_id = session.id
        return session

    def add_frame(self, session: Session, jpeg: bytes) -> int:
        frame_id = next(self._frame_ids)
        self._frames[frame_id] = (session.id, now_ms())
        while len(self._frames) > RECENT_FRAMES:
            self._frames.popitem(last=False)
        session.latest, session.latest_id = jpeg, frame_id
        session.frames_received += 1
        session.touch()
        session.new_frame.set()
        return frame_id

    def frame(self, frame_id: int) -> tuple[Session | None, int | None]:
        """frame_id로 (세션, gateway가 받은 시각 ms)를 찾는다."""
        sid, received = self._frames.get(frame_id, (None, None))
        return (self.get(sid) if sid else None), received

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


async def send_all(sockets: set[WebSocket], text: str) -> int:
    """여러 화면에 텍스트를 보낸다. 2초 안에 못 받는 화면은 연결을 정리한다. 보낸 화면 수를 돌려준다."""
    delivered = 0
    for ws in list(sockets):
        try:
            await asyncio.wait_for(ws.send_text(text), timeout=2.0)
            delivered += 1
        except Exception:
            sockets.discard(ws)
    return delivered


async def broadcast(session: Session, payload: dict[str, Any]) -> int:
    """세션의 점검 화면에 JSON을 보낸다."""
    if not session.viewers:
        return 0
    return await send_all(session.viewers, json.dumps(payload, ensure_ascii=False))


def frame_id_of(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def send_stage_result(
    session: Session,
    frame_id: int,
    image_b64: str,
    result: dict[str, Any],
    stage: str,
    latency_ms: int | None = None,
) -> int:
    """점검 화면에 단계별 결과를 보낸다. stage: final · preprocessing · echo"""
    payload = {
        "type": "result",
        "stage": stage,
        "frame_id": frame_id,
        "latency_ms": latency_ms,
        "result": result,
        "image": data_uri(image_b64),
    }
    session.results += 1
    session.last_result = payload
    session.touch()
    return await broadcast(session, payload)


async def relay_result(store: SessionStore, payload: dict[str, Any], stage: str = "final") -> int:
    """visualizer 형식 결과를 web 대시보드에 그대로 보내고, 그 프레임의 세션 점검 화면에도 보낸다.

    payload: {"frame_id", "timestamp", "latency_ms", "image"(base64), "summary", "objects"}
    """
    text = json.dumps(payload, ensure_ascii=False)
    store.last_dashboard = text
    delivered = await send_all(store.dashboards, text)

    frame_id = frame_id_of(payload.get("frame_id"))
    session, received = store.frame(frame_id) if frame_id is not None else (None, None)
    if session is not None:
        latency = payload.get("latency_ms")
        if latency is None and received is not None:
            latency = now_ms() - received
        result = {k: v for k, v in payload.items() if k != "image"}
        delivered += await send_stage_result(session, frame_id, payload["image"], result, stage, latency)
    return delivered
