"""HTTP 로 상태 보여 주기: /status(JSON), /metrics(Prometheus 텍스트), /(웹 화면)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .judge import LEVELS

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pimon</title>
<style>
body{font-family:sans-serif;margin:1.5em;max-width:40em}
td,th{padding:.3em .8em;text-align:left;border-bottom:1px solid #ddd}
.ok{color:#1a7f37}.warn{color:#9a6700}.crit{color:#cf222e;font-weight:bold}.unknown{color:#6e7781}
</style></head><body>
<h1>pimon <span id="level"></span></h1>
<p id="meta"></p>
<table><thead><tr><th>항목</th><th>값</th><th>판정</th><th>설명</th></tr></thead>
<tbody id="rows"></tbody></table>
<script>
async function tick(){
  try{
    const s = await (await fetch('status')).json();
    document.getElementById('level').textContent = s.level;
    document.getElementById('level').className = s.level;
    document.getElementById('meta').textContent = s.host + ' · ' + s.time;
    const rows = Object.entries(s.checks).map(([k, c]) =>
      `<tr><td>${k}</td><td>${c.value ?? ''} ${c.unit ?? ''}</td>` +
      `<td class="${c.level}">${c.level}</td><td>${c.detail}</td></tr>`);
    document.getElementById('rows').innerHTML = rows.join('');
  }catch(e){
    document.getElementById('level').textContent = '연결 끊김';
    document.getElementById('level').className = 'unknown';
  }
}
tick(); setInterval(tick, 5000);
</script></body></html>
"""


class State:
    """수집 고리와 HTTP 처리기가 함께 보는 최신 상태."""

    def __init__(self):
        self._lock = threading.Lock()
        self._snapshot = None

    def set(self, snapshot):
        with self._lock:
            self._snapshot = snapshot

    def get(self):
        with self._lock:
            return self._snapshot


def metrics_text(snap):
    lines = [
        "# HELP pimon_level 0=ok 1=unknown 2=warn 3=crit",
        "# TYPE pimon_level gauge",
        f"pimon_level {LEVELS.index(snap['level'])}",
    ]
    for name, item in snap["checks"].items():
        lines.append(f'pimon_check_level{{check="{name}"}} {LEVELS.index(item["level"])}')
        if isinstance(item.get("value"), (int, float)):
            lines.append(f'pimon_value{{check="{name}"}} {item["value"]}')
    return "\n".join(lines) + "\n"


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, ctype, body):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            snap = state.get()
            if path == "/":
                self._send(200, "text/html; charset=utf-8", PAGE)
            elif snap is None and path in ("/status", "/metrics"):
                self._send(503, "text/plain; charset=utf-8", "아직 첫 수집 전입니다\n")
            elif path == "/status":
                self._send(200, "application/json", json.dumps(snap, ensure_ascii=False))
            elif path == "/metrics":
                self._send(200, "text/plain; version=0.0.4", metrics_text(snap))
            else:
                self._send(404, "text/plain; charset=utf-8", "없는 주소\n")

        def log_message(self, fmt, *args):
            pass

    return Handler


def start(state, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(state))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd
