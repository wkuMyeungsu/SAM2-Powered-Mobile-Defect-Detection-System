"""gateway 테스트. 실제 휴대폰·터널·다른 파트 없이 TestClient와 가짜 객체로 돈다.

실행 (저장소 루트에서): docker compose run --rm gateway pytest -q
"""
import asyncio
import base64
import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app
from app.sessions import jpeg_size
from app.tunnel import URL_RE, Tunnel

# 실제 인코더(Pillow)로 만든 40x30 JPEG: 일반(baseline), progressive
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wAARCAAeACgDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDYooorwT2AooooAKKKKACiiigAooooAKKKKAP/2Q=="
)
JPEG_PROGRESSIVE = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAoHBwgHBgoICAgLCgoLDhgQDg0NDh0VFhEYIx8lJCIfIiEmKzcvJik0KSEiMEExNDk7Pj4+JS5ESUM8SDc9Pjv/2wBDAQoLCw4NDhwQEBw7KCIoOzs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozs7Ozv/wgARCAAeACgDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAT/xAAVAQEBAAAAAAAAAAAAAAAAAAAABP/aAAwDAQACEAMQAAABsEFgAAAAH//EABQQAQAAAAAAAAAAAAAAAAAAAED/2gAIAQEAAQUCJ//EABQRAQAAAAAAAAAAAAAAAAAAACD/2gAIAQMBAT8BX//EABQRAQAAAAAAAAAAAAAAAAAAACD/2gAIAQIBAT8BX//EABQQAQAAAAAAAAAAAAAAAAAAAED/2gAIAQEABj8CJ//EABQQAQAAAAAAAAAAAAAAAAAAAED/2gAIAQEAAT8hJ//aAAwDAQACAAMAAAAQAAAAAA//xAAUEQEAAAAAAAAAAAAAAAAAAAAg/9oACAEDAQE/EF//xAAUEQEAAAAAAAAAAAAAAAAAAAAg/9oACAECAQE/EF//xAAUEAEAAAAAAAAAAAAAAAAAAABA/9oACAEBAAE/ECf/2Q=="
)


def make_client(monkeypatch, **env):
    defaults = {"GATEWAY_TUNNEL": "off", "GATEWAY_FORWARD_MODE": "echo", "GATEWAY_PREVIEW_FPS": "1000", "GATEWAY_PUBLIC_URL": ""}
    for key, value in {**defaults, **env}.items():
        monkeypatch.setenv(key, value)
    return TestClient(app)


def receive_until(ws, kind, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"{kind} 메시지를 받지 못함")


def wait_for(condition, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_jpeg_size():
    assert jpeg_size(JPEG) == (40, 30)
    assert jpeg_size(JPEG_PROGRESSIVE) == (40, 30)
    assert jpeg_size(b"not a jpeg") is None
    assert jpeg_size(JPEG[:20]) is None


def test_health_and_session(monkeypatch):
    with make_client(monkeypatch) as c:
        body = c.get("/health").json()
        assert body["status"] == "ok" and body["tunnel"]["status"] == "off" and body["forward_mode"] == "echo"

        r = c.post("/api/sessions")
        assert r.status_code == 201
        info = r.json()
        assert re.fullmatch(r"[a-z2-9]{6}", info["session_id"])
        assert info["phone_url"] is None  # 터널이 꺼져 있으면 휴대폰 주소가 없다
        assert info["phone_url_local"].endswith(f"/phone?s={info['session_id']}")
        assert info["view_url"].startswith("ws://") and info["view_url"].endswith(f"/ws/view/{info['session_id']}")
        assert c.get(f"/api/sessions/{info['session_id']}/qr.svg").status_code == 503
        assert c.get("/api/sessions/nope00").status_code == 404
        assert c.get("/phone").status_code == 200 and c.get("/").status_code == 200


def test_qr_uses_public_url(monkeypatch):
    with make_client(monkeypatch, GATEWAY_PUBLIC_URL="https://demo.example.com/") as c:
        info = c.post("/api/sessions").json()
        assert info["phone_url"] == f"https://demo.example.com/phone?s={info['session_id']}"
        r = c.get(f"/api/sessions/{info['session_id']}/qr.svg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert r.content.startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')  # 없으면 <img>에서 깨진다


def test_unknown_session_closes_4404(monkeypatch):
    with make_client(monkeypatch) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/phone/nope00") as ws:
                ws.receive_json()
        assert e.value.code == 4404


def test_echo_mode_end_to_end(monkeypatch):
    """휴대폰 → gateway → (echo) → 보는 화면: 설정, 미리보기, 결과가 순서대로 온다."""
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view:
            assert view.receive_json()["phone_connected"] is False
            with c.websocket_connect(f"/ws/phone/{sid}") as phone:
                assert phone.receive_json() == {"type": "config", "fps": 5, "max_side": 1024, "quality": 0.7}
                phone.send_text(json.dumps({"type": "hello", "width": 40, "height": 30, "user_agent": "test"}))
                phone.send_bytes(JPEG)
                frame = receive_until(view, "frame")
                assert frame["frame_id"] == 1 and frame["image"].startswith("data:image/jpeg;base64,")
                result = receive_until(view, "result")
                assert result["frame_id"] == 1 and result["stage"] == "echo"
                assert result["latency_ms"] is not None and result["latency_ms"] >= 0
                status = receive_until(view, "status")
                assert status["phone_connected"] is True and status["phone_info"]["width"] == 40


def test_drops_non_jpeg(monkeypatch):
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(b"\x00" * 100)
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["frames_received"] == 1)
            assert c.get(f"/api/sessions/{sid}").json()["frames_dropped"] == 1


def test_second_phone_replaces_first(monkeypatch):
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as first:
            first.receive_json()
            with c.websocket_connect(f"/ws/phone/{sid}") as second:
                second.receive_json()
                with pytest.raises(WebSocketDisconnect) as e:
                    first.receive_json()
                assert e.value.code == 4409


class FakePreprocessing:
    """preprocessing 대신 요청을 기록한다.

    sync=True면 진아 님 POST /preprocess처럼 200으로 전처리 결과(이미지 포함)를 바로 돌려주고,
    False면 202만 돌려준다 (뒤 단계가 POST /results로 결과를 보내는 방식).
    """

    def __init__(self, status=202, sync=False):
        self.status = status
        self.sync = sync
        self.requests = []

    def __call__(self, request: httpx.Request):
        body = request.read()
        self.requests.append({
            "url": str(request.url),
            "content_type": request.headers.get("content-type"),
            "frame_id": int(request.headers["x-frame-id"]),
            "session_id": request.headers["x-session-id"],
            "received_at": int(request.headers["x-received-at"]),
            "body_is_frame": body == JPEG,
        })
        if self.status >= 400:
            return httpx.Response(self.status, json={"error": "not supported"})
        if self.sync:
            return httpx.Response(200, json={
                "frame_id": request.headers["x-frame-id"],
                "image": base64.b64encode(JPEG_PROGRESSIVE).decode(),
                "format": "jpeg", "width": 40, "height": 30, "scale": 1.0,
                "candidates": [{"bbox": [5, 5, 20, 20], "point": [12.5, 12.5], "area": 225}],
                "elapsed_ms": 3.1,
            })
        return httpx.Response(self.status)


def use_fake(client, fake):
    client.app.state.forwarder.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))


def post_result(client, meta, result=None):
    return client.post(
        "/results",
        files={"image": ("marked.jpg", JPEG, "image/jpeg")},
        data={"meta": json.dumps(meta), "result": json.dumps(result if result is not None else {"verdict": "defect"})},
    )


def test_pipeline_sends_latest_frame_only(monkeypatch):
    """처리 중인 프레임이 있으면 기다렸다가, 결과가 오면 그 사이 쌓인 것 중 최신 1장만 보낸다."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline", GATEWAY_FORWARD_INTERVAL="0") as c:
        fake = FakePreprocessing()
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: len(fake.requests) == 1)
            first = fake.requests[0]
            assert first["url"] == "http://preprocessing:8002/preprocess"
            assert first["content_type"] == "image/jpeg" and first["body_is_frame"]  # 원본 JPEG 그대로
            assert first["session_id"] == sid and first["frame_id"] == 1 and first["received_at"] > 0

            phone.send_bytes(JPEG)
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["frames_received"] == 3)
            time.sleep(0.2)
            assert len(fake.requests) == 1  # 결과가 오기 전에는 더 보내지 않는다

            r = post_result(c, {"session_id": sid, "frame_id": 1}, {"verdict": "defect", "items": [{"id": 1, "verdict": "defect"}]})
            assert r.status_code == 200 and r.json()["delivered"] == 1
            result = receive_until(view, "result")
            assert result["frame_id"] == 1 and result["stage"] == "final" and result["result"]["verdict"] == "defect"

            assert wait_for(lambda: len(fake.requests) == 2)
            assert fake.requests[1]["frame_id"] == 3  # 2번은 건너뛰고 최신 3번



def test_sync_preprocessing_result_goes_to_view(monkeypatch):
    """preprocessing이 200으로 전처리 결과를 바로 주면 화면에 보내고, 기다리지 않고 다음 프레임을 보낸다."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline", GATEWAY_FORWARD_INTERVAL="0", GATEWAY_FORWARD_TIMEOUT="30") as c:
        fake = FakePreprocessing(sync=True)
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            result = receive_until(view, "result")
            assert result["stage"] == "preprocessing" and result["frame_id"] == 1
            assert result["image"] == "data:image/jpeg;base64," + base64.b64encode(JPEG_PROGRESSIVE).decode()  # 전처리된 이미지
            assert result["result"]["candidates"][0]["bbox"] == [5, 5, 20, 20] and "image" not in result["result"]
            phone.send_bytes(JPEG)
            assert wait_for(lambda: len(fake.requests) == 2, timeout=2)  # 결과 대기 시간(30초)을 기다리지 않는다
            assert c.get(f"/api/sessions/{sid}").json()["forward_timeouts"] == 0

def test_pipeline_error_is_recorded(monkeypatch):
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        use_fake(c, FakePreprocessing(status=501))  # 지금 preprocessing 뼈대처럼 POST를 처리하지 못하는 경우
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["forward_errors"] == 1)
            info = c.get(f"/api/sessions/{sid}").json()
            assert "501" in info["last_error"] and info["inflight_frame_id"] is None


def test_timeout_frees_slot(monkeypatch):
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline", GATEWAY_FORWARD_TIMEOUT="0.3", GATEWAY_FORWARD_INTERVAL="0") as c:
        fake = FakePreprocessing()
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: len(fake.requests) == 1)
            phone.send_bytes(JPEG)
            assert wait_for(lambda: len(fake.requests) == 2, timeout=2)  # 결과 없이 0.3초 뒤 다음 프레임
            assert c.get(f"/api/sessions/{sid}").json()["forward_timeouts"] == 1


def test_results_validation(monkeypatch):
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        assert post_result(c, {"frame_id": 1}).status_code == 400  # session_id 없음
        assert post_result(c, {"session_id": sid, "frame_id": "x"}).status_code == 400
        assert post_result(c, {"session_id": sid, "frame_id": 1}, result=[1, 2]).status_code == 400
        assert post_result(c, {"session_id": "nope00", "frame_id": 1}).status_code == 404
        assert post_result(c, {"session_id": sid, "frame_id": 1}).json() == {"delivered": 0}


def test_new_viewer_gets_last_result(monkeypatch):
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        post_result(c, {"session_id": sid, "frame_id": 7}, {"verdict": "ok"})
        with c.websocket_connect(f"/ws/view/{sid}") as view:
            assert view.receive_json()["type"] == "status"
            last = view.receive_json()
            assert last["type"] == "result" and last["frame_id"] == 7


# ----- 터널 -----

def run_tunnel(tmp_path, script: str | None, wait=3.0):
    async def go():
        binary = "no-such-cloudflared"
        if script is not None:
            path = tmp_path / "fake-cloudflared"
            path.write_text("#!/bin/sh\n" + script)
            path.chmod(0o755)
            binary = str(path)
        tunnel = Tunnel("cloudflared", 8001, binary=binary)
        await tunnel.start()
        end = asyncio.get_running_loop().time() + wait
        while tunnel.status == "starting" and asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.05)
        info = tunnel.info()
        await tunnel.stop()
        return info
    return asyncio.run(go())


def test_tunnel_missing_binary(tmp_path):
    info = run_tunnel(tmp_path, None)
    assert info["status"] == "error" and "cloudflared" in info["error"]


def test_tunnel_finds_url(tmp_path):
    info = run_tunnel(tmp_path, 'echo "INF |  https://brave-snack-line.trycloudflare.com  |" >&2\nsleep 5\n')
    assert info == {"status": "ready", "url": "https://brave-snack-line.trycloudflare.com", "error": None}


def test_tunnel_reports_failure(tmp_path):
    # 실제 cloudflared가 네트워크가 막혔을 때 남기는 줄. 이 안의 api.trycloudflare.com을 공개 주소로 잡으면 안 된다
    line = 'failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": dial tcp: lookup api.trycloudflare.com: no such host'
    assert URL_RE.search(line) is None
    assert URL_RE.search("INF |  https://brave-snack-line.trycloudflare.com  |").group(0) == "https://brave-snack-line.trycloudflare.com"
    info = run_tunnel(tmp_path, f"echo '{line}' >&2\nexit 1\n")
    assert info["status"] == "error" and info["url"] is None and "no such host" in info["error"]
