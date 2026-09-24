"""m3.com を模したローカルサイトで一連の流れを確認するテスト。"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from m3_campaign.runner import load_config, matches, run, DEFAULT_CONFIG

STATE = {"logged_in": False, "bonus": False, "entered": set(), "survey_clicked": False}

LOGIN_FORM = """<form method="post" action="/login">
<input type="text" name="loginId"><input type="password" name="password">
<button type="submit">ログイン</button></form>"""


def page(body: str) -> bytes:
    return f"<html><body>{body}</body></html>".encode()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, body: bytes, status: int = 200, location: str | None = None):
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        path = urlparse(self.path).path
        if path == "/login":
            if form.get("loginId") == ["doctor"] and form.get("password") == ["secret"]:
                STATE["logged_in"] = True
                return self.send(b"", 302, "/")
            return self.send(page("エラー" + LOGIN_FORM))
        if path == "/bonus":
            STATE["bonus"] = True
            return self.send(b"", 302, "/")
        if path.startswith("/campaign/") and path.endswith("/entry"):
            STATE["entered"].add(path.split("/")[2])
            return self.send(b"", 302, path.rsplit("/", 1)[0])
        if path == "/survey/answer":
            STATE["survey_clicked"] = True
            return self.send(b"", 302, "/survey")
        self.send(b"", 404)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/login" or not STATE["logged_in"]:
            return self.send(page(LOGIN_FORM))
        if path == "/":
            bonus = "受け取り済み" if STATE["bonus"] else \
                '<form method="post" action="/bonus"><button>ポイントを受け取る</button></form>'
            return self.send(page(bonus + """
                <a href="/campaign/a">キャンペーンA</a>
                <a href="/campaign/b">キャンペーンB</a>
                <a href="/survey">アンケートでポイント</a>
                <a href="https://example.com/campaign/x">外部</a>"""))
        if path.startswith("/campaign/"):
            cid = path.split("/")[2]
            if cid in STATE["entered"]:
                return self.send(page("エントリー済みです"))
            return self.send(page(f"""<form method="post" action="/campaign/{cid}/entry">
                <button>エントリーする</button></form><button>動画を視聴してポイント</button>"""))
        if path == "/survey":
            return self.send(page("""<form method="post" action="/survey/answer">
                <button>アンケートに回答して参加する</button></form>"""))
        self.send(b"", 404)


@pytest.fixture
def site():
    STATE.update(logged_in=False, bonus=False, entered=set(), survey_clicked=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    cfg = load_config(DEFAULT_CONFIG)
    cfg["login"]["url"] = f"{base}/login"
    cfg["login_bonus"]["urls"] = [f"{base}/"]
    cfg["campaigns"]["urls"] = []
    cfg["campaigns"]["discover"] = [{"page": f"{base}/", "link_pattern": "(campaign|survey)"}]
    cfg["wait_ms"] = 0
    cfg["timeout_ms"] = 5000
    yield cfg
    server.shutdown()


def test_matches():
    cc = load_config(DEFAULT_CONFIG)["campaigns"]
    assert matches("エントリーする", cc["button_texts"], cc["exclude_texts"])
    assert not matches("エントリー済み", cc["button_texts"], cc["exclude_texts"])
    assert not matches("アンケートに回答して参加する", cc["button_texts"], cc["exclude_texts"])
    assert not matches("動画を視聴して応募する", cc["button_texts"], cc["exclude_texts"])


def test_full_run(site):
    report = run(site, "doctor", "secret")
    assert report.logged_in and not report.failed
    assert STATE["bonus"]
    assert STATE["entered"] == {"a", "b"}
    assert not STATE["survey_clicked"]

    # 2回目は参加済みとして扱われ、何も押さない
    report = run(site, "doctor", "secret")
    statuses = {r.page.rsplit("/", 1)[-1]: r.status for r in report.results if r.action == "エントリー"}
    assert statuses["a"] == "already" and statuses["b"] == "already"


def test_dry_run_does_not_click(site):
    report = run(site, "doctor", "secret", dry_run=True)
    assert not STATE["bonus"] and not STATE["entered"]
    assert any(r.status == "dry-run" for r in report.results)


def test_wrong_password(site):
    report = run(site, "doctor", "wrong")
    assert not report.logged_in and report.failed
