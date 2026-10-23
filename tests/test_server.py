import json
import urllib.error
import urllib.request

import pytest

from pimon import __main__ as main, config, server


@pytest.fixture
def running(pi):
    cfg = config.load()
    cfg["root"] = str(pi)
    state = server.State()
    httpd = server.start(state, "127.0.0.1", 0)
    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield cfg, state, url
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers["Content-Type"], r.read().decode()


def test_before_first_collect_is_503(running):
    _, _, url = running
    with pytest.raises(urllib.error.HTTPError) as e:
        get(url + "/status")
    assert e.value.code == 503


def test_status_metrics_page(running):
    cfg, state, url = running
    state.set(main.snapshot(cfg))
    code, ctype, body = get(url + "/status")
    assert code == 200 and ctype == "application/json"
    assert json.loads(body)["checks"]["temp"]["value"] == 48.3
    _, _, body = get(url + "/metrics")
    assert 'pimon_value{check="temp"} 48.3' in body
    assert "pimon_level 0" in body
    _, ctype, body = get(url + "/")
    assert ctype.startswith("text/html") and "fetch('status')" in body


def test_404(running):
    _, _, url = running
    with pytest.raises(urllib.error.HTTPError) as e:
        get(url + "/nope")
    assert e.value.code == 404
