"""HTTP: GET /api/status (JSON), GET / (page.html).

make_server(get_status, bind, port) 는 서버 객체만 만들어 돌려준다 (serve 하지 않음).
호출하는 쪽:
    srv = make_server(monitor.status, "127.0.0.1", 8080)
    threading.Thread(target=srv.serve_forever).start()
    ...
    srv.shutdown(); srv.server_close()

get_status() 는 인자 없이 dict 또는 None(아직 수집 전)을 돌려준다.
"""

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

PAGE_PATH = Path(__file__).with_name("page.html")
ALLOW = "GET, HEAD"


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "pimon"
    sys_version = ""
    timeout = 10  # 느린 클라이언트가 스레드를 오래 붙잡지 않게

    # --- 응답 ---

    def _send(self, code, ctype, body, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, code, obj, extra=None):
        self._send(code, "application/json; charset=utf-8", _json_bytes(obj), extra)

    def _error(self, code, message, extra=None):
        self._send_json(code, {"error": message}, extra)

    # --- 경로 ---

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            self._status()
        elif path == "/":
            self._send(HTTPStatus.OK, "text/html; charset=utf-8", self.server.page)
        else:
            self._error(HTTPStatus.NOT_FOUND, "not found")

    do_HEAD = do_GET

    def _status(self):
        try:
            status = self.server.get_status()
            if status is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "아직 수집 전")
                return
            body = _json_bytes(status)
        except Exception:
            log.exception("get_status 실패")
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "상태를 만들지 못함")
            return
        self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)

    def _method_not_allowed(self):
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method not allowed", {"Allow": ALLOW})

    def __getattr__(self, name):
        # GET/HEAD 외 모든 메서드(do_POST, do_PUT, ...)는 501 대신 405.
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    # --- 로그 ---

    # 화면이 5초마다 묻으므로 요청마다 INFO 로 남기면 journald 가 찬다. 요청은 DEBUG, 오류만 WARNING.
    def log_message(self, format, *args):
        log.debug("%s %s", self.address_string(), format % args)

    def log_error(self, format, *args):
        log.warning("%s %s", self.address_string(), format % args)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, get_status, page):
        self.get_status = get_status
        self.page = page
        super().__init__(addr, _Handler)


def make_server(get_status, bind, port):
    """서버 객체를 만들어 돌려준다. serve_forever 는 호출하는 쪽이 띄운다."""
    page = PAGE_PATH.read_bytes()
    return _Server((bind, port), get_status, page)
