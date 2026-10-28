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
    defaults = {"GATEWAY_TUNNEL": "off", "GATEWAY_FORWARD_MODE": "echo", "GATEWAY_PREVIEW_FPS": "1000", "GATEWAY_PUBLIC_URL": "",
                "GATEWAY_FORWARD_INTERVAL": "0", "GATEWAY_INFERENCE_URL": "http://inference:8003/infer"}
    for key, value in {**defaults, **env}.items():
        monkeypatch.setenv(key, value)
    return TestClient(app)


def receive_until(ws, kind, limit=20):
    for _ in range(limit):
        msg = ws.receive_json()
        if msg.get("type") == kind:
            return msg
    raise AssertionError(f"{kind} 메시지를 받지 못함")


def wait_for(condition, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def visualizer_result(frame_id, **extra):
    """visualizer가 POST /result로 보내는 형식 (services/visualizer/contracts/visualizer-gateway.md)."""
    return {
        "frame_id": frame_id, "timestamp": time.time() - 0.2, "latency_ms": 200,
        "image": base64.b64encode(JPEG_PROGRESSIVE).decode(),
        "summary": {"in_frame": 3, "defect_in_frame": 1, "passed_total": 10, "defect_total": 2},
        "objects": [{"id": 17, "result": "FAIL", "circularity": 0.62}],
        **extra,
    }


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


def test_qr_uses_public_url_and_default_session(monkeypatch):
    with make_client(monkeypatch, GATEWAY_PUBLIC_URL="https://demo.example.com/") as c:
        info = c.post("/api/sessions").json()
        assert info["phone_url"] == f"https://demo.example.com/phone?s={info['session_id']}"
        r = c.get(f"/api/sessions/{info['session_id']}/qr.svg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert r.content.startswith(b'<svg xmlns="http://www.w3.org/2000/svg"')  # 없으면 <img>에서 깨진다

        # web 대시보드용: 세션을 따로 만들지 않아도 기본 세션 QR을 준다. 여러 번 불러도 같은 세션
        assert c.get("/qr.svg").status_code == 200
        first, second = c.get("/api/default-session").json(), c.get("/api/default-session").json()
        assert first["session_id"] == second["session_id"] != info["session_id"]


def test_unknown_session_closes_4404(monkeypatch):
    with make_client(monkeypatch) as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect("/ws/phone/nope00") as ws:
                ws.receive_json()
        assert e.value.code == 4404


def test_echo_mode_end_to_end(monkeypatch):
    """휴대폰 → gateway → (echo) → 점검 화면과 web 대시보드."""
    with make_client(monkeypatch) as c:
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect("/ws/view") as dashboard, c.websocket_connect(f"/ws/view/{sid}") as view:
            assert view.receive_json()["phone_connected"] is False
            with c.websocket_connect(f"/ws/phone/{sid}") as phone:
                assert phone.receive_json() == {"type": "config", "fps": 5, "max_side": 1024, "quality": 0.7}
                phone.send_text(json.dumps({"type": "hello", "width": 40, "height": 30, "user_agent": "test"}))
                phone.send_bytes(JPEG)
                frame = receive_until(view, "frame")
                assert frame["image"].startswith("data:image/jpeg;base64,")
                result = receive_until(view, "result")
                assert result["frame_id"] == frame["frame_id"] and result["stage"] == "echo" and result["latency_ms"] >= 0
                status = receive_until(view, "status")
                assert status["phone_connected"] is True and status["phone_info"]["width"] == 40

                # web 대시보드는 visualizer 형식 그대로 받는다 (type 필드 없음, image는 data: 없는 base64)
                d = dashboard.receive_json()
                assert d["frame_id"] == frame["frame_id"] and d["summary"]["passed_total"] == 0 and d["objects"] == []
                assert base64.b64decode(d["image"]) == JPEG and isinstance(d["timestamp"], float)


def test_frame_ids_increase_across_sessions(monkeypatch):
    """visualizer는 frame_id 순서로 오래된 프레임을 버리므로, 세션이 바뀌어도 번호가 되돌아가면 안 된다."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="off") as c:
        ids = []
        for _ in range(2):
            sid = c.post("/api/sessions").json()["session_id"]
            with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
                phone.receive_json()
                phone.send_bytes(JPEG)
                ids.append(receive_until(view, "frame")["frame_id"])
        assert ids[1] > ids[0]


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


class FakeStages:
    """preprocessing(POST /preprocess)과 inference(POST /infer) 대신 요청을 기록한다."""

    def __init__(self, pre_status=200, infer_status=200, infer_delay=0.0):
        self.pre_status, self.infer_status, self.infer_delay = pre_status, infer_status, infer_delay
        self.pre, self.infer = [], []

    async def __call__(self, request: httpx.Request):
        body = await request.aread()
        if request.url.path == "/preprocess":
            self.pre.append({"url": str(request.url), "content_type": request.headers.get("content-type"),
                             "frame_id": request.headers["x-frame-id"], "session_id": request.headers["x-session-id"],
                             "timestamp": float(request.headers["x-timestamp"]), "body_is_frame": body == JPEG})
            if self.pre_status != 200:
                return httpx.Response(self.pre_status, json={"error": "not supported"})
            return httpx.Response(200, json={  # 진아 님 POST /preprocess 응답 형식
                "frame_id": request.headers["x-frame-id"], "image": base64.b64encode(JPEG_PROGRESSIVE).decode(),
                "format": "jpeg", "width": 40, "height": 30, "original_width": 40, "original_height": 30, "scale": 1.0,
                "candidates": [{"bbox": [5, 5, 20, 20], "point": [12.5, 12.5], "area": 225}], "elapsed_ms": 3.1,
            })
        if request.url.path == "/infer":
            self.infer.append(json.loads(body))
            if self.infer_delay:
                await asyncio.sleep(self.infer_delay)
            if self.infer_status != 200:
                return httpx.Response(self.infer_status, json={"error": "busy"})
            return httpx.Response(200, json={"frame_id": self.infer[-1]["frame_id"], "objects": [], "visualizer": "ok"})
        return httpx.Response(404)


def use_fake(client, fake):
    client.app.state.forwarder.client = httpx.AsyncClient(transport=httpx.MockTransport(fake))


def test_pipeline_calls_preprocessing_then_inference(monkeypatch):
    """② JPEG를 /preprocess로 → ③ 그 응답에 frame_id·timestamp·원본을 붙여 /infer로 → ⑤ visualizer /result → ⑥ 화면."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        fake = FakeStages()
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect("/ws/view") as dashboard, c.websocket_connect(f"/ws/view/{sid}") as view, \
                c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: len(fake.infer) == 1)
            pre, inf = fake.pre[0], fake.infer[0]
            assert pre["url"] == "http://preprocessing:8002/preprocess"
            assert pre["content_type"] == "image/jpeg" and pre["body_is_frame"] and pre["session_id"] == sid
            assert inf["frame_id"] == pre["frame_id"] and isinstance(inf["frame_id"], str)  # 문자열 번호 (preprocessing과 같게)
            assert abs(inf["timestamp"] - time.time()) < 5 and inf["timestamp"] == pytest.approx(pre["timestamp"], abs=0.002)
            assert base64.b64decode(inf["original_image"]) == JPEG  # visualizer가 원본에 그리도록
            assert inf["candidates"][0]["bbox"] == [5, 5, 20, 20] and inf["width"] == 40  # 전처리 응답 그대로

            r = c.post("/result", json=visualizer_result(inf["frame_id"]))
            assert r.status_code == 200 and r.json()["delivered"] == 2  # 대시보드 1 + 점검 화면 1
            d = dashboard.receive_json()
            assert d["frame_id"] == inf["frame_id"] and d["summary"]["defect_in_frame"] == 1  # 그대로 전달
            result = receive_until(view, "result")
            assert result["stage"] == "final" and result["frame_id"] == int(inf["frame_id"])
            assert result["result"]["objects"][0]["result"] == "FAIL" and "image" not in result["result"]


def test_pipeline_sends_latest_frame_only(monkeypatch):
    """추론 중에 들어온 프레임은 최신 1장만 남고, 추론이 끝나면 그 1장을 보낸다."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        fake = FakeStages(infer_delay=0.5)
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            first = receive_until(view, "frame")["frame_id"]
            assert wait_for(lambda: len(fake.infer) == 1)
            phone.send_bytes(JPEG)
            phone.send_bytes(JPEG)
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["frames_received"] == 4)
            assert wait_for(lambda: len(fake.infer) == 2, timeout=3)
            assert int(fake.infer[1]["frame_id"]) == first + 3  # 중간 2장은 건너뛰고 최신


def test_inference_busy_is_skipped_not_error(monkeypatch):
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        use_fake(c, FakeStages(infer_status=503))
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["inference_busy"] == 1)
            assert c.get(f"/api/sessions/{sid}").json()["forward_errors"] == 0


def test_without_inference_shows_preprocessing(monkeypatch):
    """GATEWAY_INFERENCE_URL을 비우면 전처리 결과(이미지, 후보 영역)를 점검 화면에 보여 준다."""
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline", GATEWAY_INFERENCE_URL="") as c:
        fake = FakeStages()
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            result = receive_until(view, "result")
            assert result["stage"] == "preprocessing" and result["result"]["candidates"][0]["bbox"] == [5, 5, 20, 20]
            assert result["image"] == "data:image/jpeg;base64," + base64.b64encode(JPEG_PROGRESSIVE).decode()
            assert fake.infer == []


def test_inference_down_records_error_and_shows_preprocessing(monkeypatch):
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        use_fake(c, FakeStages(infer_status=500))
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/view/{sid}") as view, c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert receive_until(view, "result")["stage"] == "preprocessing"
            assert wait_for(lambda: "inference 응답 500" in (c.get(f"/api/sessions/{sid}").json()["last_error"] or ""))


def test_preprocessing_error_is_recorded(monkeypatch):
    with make_client(monkeypatch, GATEWAY_FORWARD_MODE="pipeline") as c:
        fake = FakeStages(pre_status=501)  # dev의 preprocessing 뼈대처럼 POST를 처리하지 못하는 경우
        use_fake(c, fake)
        sid = c.post("/api/sessions").json()["session_id"]
        with c.websocket_connect(f"/ws/phone/{sid}") as phone:
            phone.receive_json()
            phone.send_bytes(JPEG)
            assert wait_for(lambda: c.get(f"/api/sessions/{sid}").json()["forward_errors"] == 1)
            info = c.get(f"/api/sessions/{sid}").json()
            assert "preprocessing 응답 501" in info["last_error"] and info["inflight_frame_id"] is None and fake.infer == []


def test_result_validation(monkeypatch):
    with make_client(monkeypatch) as c:
        assert c.post("/result", json={"image": "abc"}).status_code == 400  # frame_id 없음
        assert c.post("/result", json={"frame_id": 1}).status_code == 400  # image 없음
        assert c.post("/result", content=b"not json", headers={"Content-Type": "application/json"}).status_code in (400, 422)
        assert c.post("/result", json=visualizer_result("999")).json() == {"delivered": 0}  # 모르는 프레임도 받는다


def test_new_viewers_get_last_result(monkeypatch):
    with make_client(monkeypatch) as c:
        c.post("/result", json=visualizer_result(7))
        with c.websocket_connect("/ws/view") as dashboard:
            assert dashboard.receive_json()["frame_id"] == 7


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
