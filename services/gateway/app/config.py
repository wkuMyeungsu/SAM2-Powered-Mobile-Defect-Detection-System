"""환경변수 설정.

루트 .env는 모든 서비스가 같이 읽으므로 gateway 전용 값은 GATEWAY_ 접두사를 붙인다.
PORT, SERVICE_NAME은 docker-compose.yml이 넣어 준다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

TUNNEL_MODES = {"cloudflared", "off"}
FORWARD_MODES = {"pipeline", "echo", "off"}


@dataclass(frozen=True)
class Settings:
    port: int
    tunnel: str  # cloudflared: 임시 HTTPS 주소 만들기 / off: 만들지 않음
    public_url: str  # 직접 정한 공개 주소. 있으면 터널 대신 쓴다
    cloudflared_bin: str
    forward_mode: str  # pipeline: preprocessing으로 보냄 / echo: 받은 프레임을 바로 결과로 돌려줌(테스트용) / off
    forward_url: str  # preprocessing 주소. /frame이면 preprocessing이 inference로 넘긴다, /preprocess면 전처리 결과만 점검 화면에 표시
    forward_interval: float  # 프레임을 보내는 최소 간격 (초)
    forward_timeout: float  # preprocessing 응답을 기다리는 최대 시간 (초)
    phone_fps: int  # 휴대폰이 보내는 초당 장수
    frame_max_side: int  # 휴대폰이 찍는 이미지의 긴 변 (px)
    jpeg_quality: float
    preview_fps: float  # web에 원본 미리보기를 보내는 초당 장수 (0이면 안 보냄)
    max_frame_bytes: int
    session_ttl: float  # 이 시간(초) 동안 아무 연결도 없으면 세션을 지운다


def _env(name: str, default: str) -> str:
    value = os.getenv(name, "").strip()
    return value or default


def load_settings() -> Settings:
    tunnel = _env("GATEWAY_TUNNEL", "cloudflared").lower()
    if tunnel not in TUNNEL_MODES:
        raise ValueError(f"GATEWAY_TUNNEL은 {sorted(TUNNEL_MODES)} 중 하나여야 합니다: {tunnel!r}")
    forward_mode = _env("GATEWAY_FORWARD_MODE", "pipeline").lower()
    if forward_mode not in FORWARD_MODES:
        raise ValueError(f"GATEWAY_FORWARD_MODE는 {sorted(FORWARD_MODES)} 중 하나여야 합니다: {forward_mode!r}")

    return Settings(
        port=int(_env("PORT", "8001")),
        tunnel=tunnel,
        public_url=os.getenv("GATEWAY_PUBLIC_URL", "").strip().rstrip("/"),
        cloudflared_bin=_env("GATEWAY_CLOUDFLARED_BIN", "cloudflared"),
        forward_mode=forward_mode,
        forward_url=_env("GATEWAY_FORWARD_URL", "http://preprocessing:8002/frame"),
        forward_interval=float(_env("GATEWAY_FORWARD_INTERVAL", "0.2")),
        forward_timeout=float(_env("GATEWAY_FORWARD_TIMEOUT", "5")),
        phone_fps=int(_env("GATEWAY_PHONE_FPS", "5")),
        frame_max_side=int(_env("GATEWAY_FRAME_MAX_SIDE", "1024")),
        jpeg_quality=float(_env("GATEWAY_JPEG_QUALITY", "0.7")),
        preview_fps=float(_env("GATEWAY_PREVIEW_FPS", "2")),
        max_frame_bytes=int(_env("GATEWAY_MAX_FRAME_BYTES", str(2 * 1024 * 1024))),
        session_ttl=float(_env("GATEWAY_SESSION_TTL", "3600")),
    )
