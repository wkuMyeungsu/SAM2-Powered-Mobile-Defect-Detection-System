"""휴대폰이 접속할 공개 HTTPS 주소.

휴대폰 브라우저는 HTTPS 주소에서만 카메라를 열어 준다. 그래서 컨테이너 안에서
cloudflared 임시 터널(https://<임의 이름>.trycloudflare.com)을 띄워 gateway로 연결한다.
GATEWAY_PUBLIC_URL을 정하면 터널 대신 그 주소를 쓴다.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

log = logging.getLogger("uvicorn.error")

URL_RE = re.compile(r"https://(?!api\.)[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com")  # 예: https://brave-snack-line.trycloudflare.com


class Tunnel:
    def __init__(self, mode: str, port: int, public_url: str = "", binary: str = "cloudflared"):
        self.mode = mode
        self.port = port
        self.binary = binary
        self.url: str | None = public_url or None
        self.status = "manual" if public_url else "off"  # off · starting · ready · manual · error
        self.error: str | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task | None = None
        self._stopping = False

    def info(self) -> dict[str, Any]:
        return {"status": self.status, "url": self.url, "error": self.error}

    async def start(self) -> None:
        if self.status == "manual" or self.mode != "cloudflared":
            return
        path = shutil.which(self.binary)
        if not path:
            self._fail(f"cloudflared 실행 파일이 없습니다 ({self.binary}). 휴대폰 접속용 HTTPS 주소를 만들 수 없습니다.")
            return
        self.status = "starting"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                path, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as e:
            self._fail(f"cloudflared를 실행하지 못했습니다: {e}")
            return
        self._reader = asyncio.create_task(self._read_logs(), name="cloudflared-logs")

    async def _read_logs(self) -> None:
        """cloudflared 로그(stderr)에서 공개 주소를 찾는다. 로그를 계속 읽어야 프로세스가 멈추지 않는다."""
        assert self._proc and self._proc.stderr
        last_line = ""
        async for raw in self._proc.stderr:
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                last_line = line
            match = URL_RE.search(line)
            if match and self.url is None:
                self.url = match.group(0)
                self.status = "ready"
                self.error = None
                log.info("휴대폰 접속 주소 준비: %s", self.url)
        code = await self._proc.wait()
        if not self._stopping:
            self.url = None
            self._fail(f"cloudflared가 종료됐습니다 (코드 {code}): {last_line[-300:]}")

    def _fail(self, message: str) -> None:
        self.status = "error"
        self.error = message
        log.error("터널 오류: %s", message)

    async def stop(self) -> None:
        self._stopping = True
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                self._proc.kill()
        if self._reader:
            # 프로세스가 끝나면 로그 읽기도 끝난다. 끝까지 기다려야 파이프가 깔끔하게 닫힌다
            try:
                await asyncio.wait_for(self._reader, timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._reader.cancel()
