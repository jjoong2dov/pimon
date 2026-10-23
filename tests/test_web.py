import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from pimon.config import DEFAULTS
from pimon.monitor import Monitor
from pimon.web import make_server

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 10, 15, 30, tzinfo=KST)

SAMPLE = {
    "time": "2026-10-23T10:15:30+09:00",
    "overall": "warn",
    "items": {
        "temp": {"value": {"c": 71.0}, "error": None, "level": "warn", "reason": "71.0°C"},
    },
}


class _Stat:
    f_blocks, f_bfree, f_bavail, f_frsize = 100, 60, 55, 4096


@pytest.fixture
def serve():
    """serve(get_status) -> base URL. 시험이 끝나면 반드시 서버를 닫는다."""
    started = []

    def start(get_status):
        srv = make_server(get_status, "127.0.0.1", 0)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        started.append((srv, t))
        return "http://127.0.0.1:%d" % srv.server_address[1]

    try:
        yield start
    finally:
        for srv, t in started:
            srv.shutdown()
            srv.server_close()
            t.join(timeout=5)


def fetch(url, method="GET"):
    """(상태 코드, 헤더, 본문 bytes). 오류 코드도 예외 없이 돌려준다."""
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.headers, r.read()
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.headers, e.read()


# --- /api/status ---

def test_status_json(serve):
    base = serve(lambda: SAMPLE)
    code, headers, body = fetch(base + "/api/status")
    assert code == 200
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body.decode("utf-8")) == SAMPLE
    assert "71.0°C" in body.decode("utf-8")  # ensure_ascii=False
    assert int(headers["Content-Length"]) == len(body)


def test_status_query_string_ignored(serve):
    base = serve(lambda: SAMPLE)
    code, _, body = fetch(base + "/api/status?x=1")
    assert code == 200
    assert json.loads(body) == SAMPLE


def test_status_none_is_503(serve):
    base = serve(lambda: None)
    code, headers, body = fetch(base + "/api/status")
    assert code == 503
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert "error" in json.loads(body)


def test_status_exception_is_500_and_server_survives(serve):
    calls = []

    def get_status():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return SAMPLE

    base = serve(get_status)
    code, headers, body = fetch(base + "/api/status")
    assert code == 500
    assert "error" in json.loads(body)
    code, _, body = fetch(base + "/api/status")
    assert code == 200
    assert json.loads(body) == SAMPLE


def test_status_nan_is_500(serve):
    base = serve(lambda: {"overall": "ok", "items": {"x": {"value": float("nan")}}})
    code, _, body = fetch(base + "/api/status")
    assert code == 500
    assert b"NaN" not in body


def test_head_status(serve):
    base = serve(lambda: SAMPLE)
    code, headers, body = fetch(base + "/api/status", method="HEAD")
    assert code == 200
    assert body == b""
    assert headers["Content-Type"] == "application/json; charset=utf-8"


# --- / ---

def test_index_page(serve):
    base = serve(lambda: SAMPLE)
    code, headers, body = fetch(base + "/")
    assert code == 200
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    html = body.decode("utf-8")
    assert "/api/status" in html
    assert "textContent" in html
    assert "innerHTML" not in html
    assert "http://" not in html and "https://" not in html  # 바깥 자원 없음
    assert "<script src" not in html


def test_index_query_string_ignored(serve):
    base = serve(lambda: SAMPLE)
    code, _, _ = fetch(base + "/?v=2")
    assert code == 200


# --- 그 밖 ---

@pytest.mark.parametrize("path", ["/nope", "/api", "/api/status/", "/page.html", "/../pimon/web.py"])
def test_unknown_path_404(serve, path):
    base = serve(lambda: SAMPLE)
    code, _, _ = fetch(base + path)
    assert code == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_other_methods_405(serve, method):
    base = serve(lambda: SAMPLE)
    code, headers, _ = fetch(base + "/api/status", method=method)
    assert code == 405
    assert "GET" in headers["Allow"]


def test_request_log_goes_to_logging(serve, caplog, capsys):
    base = serve(lambda: SAMPLE)
    with caplog.at_level("DEBUG", logger="pimon.web"):
        fetch(base + "/api/status")
    assert any("/api/status" in r.getMessage() for r in caplog.records)
    assert all(r.levelname == "DEBUG" for r in caplog.records)    # 5초마다 오는 요청으로 로그를 채우지 않음
    assert "/api/status" not in capsys.readouterr().err


def test_make_server_does_not_serve_yet():
    srv = make_server(lambda: SAMPLE, "127.0.0.1", 0)
    try:
        assert srv.server_address[1] != 0
    finally:
        srv.server_close()


# --- 실제 Monitor 와 이어 붙이기 ---

def test_with_real_monitor(serve, fake_pi):
    mon = Monitor(fake_pi, DEFAULTS, statvfs=lambda p: _Stat(), now=lambda: NOW)
    base = serve(mon.status)
    code, _, _ = fetch(base + "/api/status")
    assert code == 503  # 아직 tick 안 함
    expected = mon.tick()
    code, _, body = fetch(base + "/api/status")
    assert code == 200
    assert json.loads(body.decode("utf-8")) == expected
    assert json.loads(body)["items"]["temp"]["reason"] == "48.3°C"


# --- 동시 연결 제한 (Codex 검토 7) ---

def test_connections_over_limit_are_closed_and_slots_come_back(serve, monkeypatch, caplog):
    """느린 연결이 자리를 다 차지하면 그 위의 연결은 스레드를 만들지 않고 바로 닫는다.
    느린 연결이 끝나면 다시 정상 응답한다."""
    import socket
    import time
    from pimon import web
    monkeypatch.setattr(web, "MAX_CONNECTIONS", 2)
    base = serve(lambda: SAMPLE)
    port = int(base.rsplit(":", 1)[1])
    slow = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]   # 요청을 보내지 않음
    try:
        time.sleep(0.2)                                     # 서버가 두 연결을 받아 스레드를 띄울 때까지
        before = threading.active_count()
        with socket.create_connection(("127.0.0.1", port), timeout=5) as extra:
            extra.sendall(b"GET /api/status HTTP/1.0\r\n\r\n")
            assert extra.recv(1024) == b""                  # 응답 없이 닫힘
        assert threading.active_count() == before
        assert "연결" in caplog.text
    finally:
        for s in slow:
            s.close()
    deadline = time.monotonic() + 5
    while True:
        try:
            code, _, _ = fetch(base + "/api/status")
            break
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)
    assert code == 200
