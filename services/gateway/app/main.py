"""gateway (C1): QR 접속, 휴대폰 프레임 받기, 처리 단계로 전달, 결과를 web으로 다시 전달.

데이터 형식: docs/contracts/frame-input.md, preprocessing-input.md, result-return.md
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import segno
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import load_settings
from .forwarder import Forwarder
from .sessions import Session, SessionStore, broadcast, data_uri, deliver_result, jpeg_size
from .tunnel import Tunnel

log = logging.getLogger("uvicorn.error")
STATIC = Path(__file__).parent / "static"
STATUS_EVERY = 2.0  # 보는 화면에 처리 현황을 보내는 간격 (초)


async def _housekeeping(app: FastAPI) -> None:
    """2초마다 화면에 현황을 보내고, 1분마다 오래된 세션을 지운다."""
    ticks = 0
    while True:
        await asyncio.sleep(STATUS_EVERY)
        ticks += 1
        for session in app.state.store:
            if session.viewers:
                await broadcast(session, session.status())
        if ticks % 30 == 0:
            for session in app.state.store.prune():
                await Forwarder.stop(session)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    app.state.settings = settings
    app.state.store = SessionStore(ttl=settings.session_ttl)
    app.state.client = httpx.AsyncClient()
    app.state.forwarder = Forwarder(settings, app.state.client)
    app.state.tunnel = Tunnel(settings.tunnel, settings.port, settings.public_url, settings.cloudflared_bin)
    await app.state.tunnel.start()
    housekeeping = asyncio.create_task(_housekeeping(app))
    log.info("gateway 시작: 전달 방식=%s → %s, 터널=%s", settings.forward_mode, settings.forward_url, app.state.tunnel.status)
    yield
    housekeeping.cancel()
    for session in app.state.store:
        await Forwarder.stop(session)
    await app.state.tunnel.stop()
    await app.state.client.aclose()


app = FastAPI(title="gateway", version="0.1.0", lifespan=lifespan)
# web 화면(http://localhost:8000)이 브라우저에서 gateway(http://localhost:8001)를 부른다
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _session_or_404(request: Request, sid: str) -> Session:
    session = request.app.state.store.get(sid)
    if session is None:
        raise HTTPException(404, f"없는 세션입니다: {sid}")
    return session


def _session_info(request: Request, session: Session) -> dict:
    tunnel = request.app.state.tunnel
    base = str(request.base_url).rstrip("/")
    ws_base = base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    return {
        "session_id": session.id,
        "phone_url": f"{tunnel.url}/phone?s={session.id}" if tunnel.url else None,
        "phone_url_local": f"{base}/phone?s={session.id}",
        "qr_url": f"{base}/api/sessions/{session.id}/qr.svg",
        "view_url": f"{ws_base}/ws/view/{session.id}",
        "tunnel": tunnel.info(),
        **{k: v for k, v in session.status().items() if k not in ("type", "session_id")},
    }


# ----- 상태 -----

@app.get("/health")
async def health(request: Request):
    s = request.app.state
    return {
        "service": "gateway",
        "status": "ok",
        "tunnel": s.tunnel.info(),
        "forward_mode": s.settings.forward_mode,
        "forward_url": s.settings.forward_url if s.settings.forward_mode == "pipeline" else None,
        "sessions": len(s.store),
    }


# ----- 페이지 -----

@app.get("/")
async def console_page():
    """세션을 만들고 QR, 원본, 결과를 확인하는 테스트 화면."""
    return FileResponse(STATIC / "console.html")


@app.get("/phone")
async def phone_page():
    """휴대폰 촬영 화면. QR 주소가 여기로 온다."""
    return FileResponse(STATIC / "phone.html")


# ----- web용 API -----

@app.post("/api/sessions", status_code=201)
async def create_session(request: Request):
    session = request.app.state.store.create()
    return _session_info(request, session)


@app.get("/api/sessions/{sid}")
async def get_session(sid: str, request: Request):
    return _session_info(request, _session_or_404(request, sid))


@app.get("/api/sessions/{sid}/qr.svg")
async def session_qr(sid: str, request: Request):
    session = _session_or_404(request, sid)
    tunnel = request.app.state.tunnel
    if not tunnel.url:
        raise HTTPException(503, f"휴대폰 접속 주소가 아직 없습니다 (터널 상태: {tunnel.status}). {tunnel.error or '잠시 뒤 다시 시도하세요.'}")
    qr = segno.make(f"{tunnel.url}/phone?s={session.id}", error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=8, border=2, xmldecl=False)  # <img>로 띄우려면 xmlns가 있는 SVG 파일 형식이어야 한다
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


# ----- 휴대폰 → gateway -----

@app.websocket("/ws/phone/{sid}")
async def phone_socket(ws: WebSocket, sid: str):
    state = ws.app.state
    settings = state.settings
    await ws.accept()
    session = state.store.get(sid)
    if session is None:
        await ws.close(code=4404, reason="unknown session")
        return

    previous, session.phone = session.phone, ws
    if previous is not None:
        try:
            await previous.close(code=4409, reason="replaced by another phone")
        except Exception:
            pass
    session.phone_info = None
    session.touch()
    await ws.send_json({"type": "config", "fps": settings.phone_fps, "max_side": settings.frame_max_side, "quality": settings.jpeg_quality})
    state.forwarder.start(session)
    await broadcast(session, session.status())

    preview_gap = 1.0 / settings.preview_fps if settings.preview_fps > 0 else None
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            data = message.get("bytes")
            if data is not None:
                size = jpeg_size(data) if len(data) <= settings.max_frame_bytes else None
                if size is None:
                    session.frames_dropped += 1
                    continue
                frame_id = session.add_frame(data, size)
                now = asyncio.get_running_loop().time()
                if preview_gap is not None and session.viewers and now - session.last_preview_at >= preview_gap:
                    session.last_preview_at = now
                    await broadcast(session, {"type": "frame", "frame_id": frame_id, "image": data_uri(data)})
            elif message.get("text"):
                try:
                    payload = json.loads(message["text"])
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("type") == "hello":
                    session.phone_info = {k: payload.get(k) for k in ("width", "height", "user_agent")}
                    await broadcast(session, session.status())
    except WebSocketDisconnect:
        pass
    finally:
        if session.phone is ws:
            session.phone = None
            session.touch()
            await Forwarder.stop(session)
            await broadcast(session, session.status())


# ----- gateway → web -----

@app.websocket("/ws/view/{sid}")
async def view_socket(ws: WebSocket, sid: str):
    await ws.accept()
    session = ws.app.state.store.get(sid)
    if session is None:
        await ws.close(code=4404, reason="unknown session")
        return
    session.viewers.add(ws)
    session.touch()
    try:
        await ws.send_json(session.status())
        if session.last_result:
            await ws.send_json(session.last_result)
        while True:  # 화면은 보내는 것이 없다. 연결이 끊기는 것만 지켜본다
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
    except WebSocketDisconnect:
        pass
    finally:
        session.viewers.discard(ws)
        session.touch()


# ----- visualizer → gateway -----

@app.post("/results")
async def receive_result(
    request: Request,
    image: UploadFile = File(..., description="판정이 표시된 JPEG"),
    meta: str = Form(..., description="preprocessing이 받은 meta JSON 그대로"),
    result: str = Form("{}", description="판정 결과 JSON"),
):
    try:
        meta_obj = json.loads(meta)
        result_obj = json.loads(result)
        sid = str(meta_obj["session_id"])
        frame_id = int(meta_obj["frame_id"])
    except (ValueError, KeyError, TypeError) as e:
        raise HTTPException(400, f"meta·result 형식 오류: {type(e).__name__}: {e}") from e
    if not isinstance(result_obj, dict):
        raise HTTPException(400, "result는 JSON 객체여야 합니다.")
    session = _session_or_404(request, sid)
    data = await image.read()
    if len(data) > request.app.state.settings.max_frame_bytes * 2:
        raise HTTPException(413, "이미지가 너무 큽니다.")
    delivered = await deliver_result(session, frame_id, data, result_obj)
    return {"delivered": delivered}
