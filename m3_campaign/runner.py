"""m3.com にログインし、ログインボーナスとエントリー型キャンペーンに自動で参加する。

使い方:
    M3_LOGIN_ID=... M3_PASSWORD=... python -m m3_campaign.runner [--config PATH] [--dry-run] [--headed]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yaml
from playwright.sync_api import Locator, Page, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

DEFAULT_CONFIG = Path(__file__).with_name("config.yaml")
CLICKABLE = 'button, a, input[type="submit"], input[type="button"], [role="button"]'


class LoginError(RuntimeError):
    pass


@dataclass
class Result:
    page: str
    action: str
    status: str  # clicked / already / none / error / dry-run
    detail: str = ""


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)
    logged_in: bool = False

    def add(self, *args: str) -> None:
        r = Result(*args)
        self.results.append(r)
        print(f"[{r.status}] {r.action} {r.page} {r.detail}".rstrip(), flush=True)

    @property
    def failed(self) -> bool:
        return not self.logged_in or any(r.status == "error" for r in self.results)

    def markdown(self) -> str:
        lines = [
            "## m3 キャンペーン自動参加",
            "",
            f"ログイン: {'成功' if self.logged_in else '失敗'}",
            "",
            "| 状態 | 種類 | ページ | 詳細 |",
            "|---|---|---|---|",
        ]
        for r in self.results:
            detail = r.detail.replace("|", "\\|")
            lines.append(f"| {r.status} | {r.action} | {r.page} | {detail} |")
        return "\n".join(lines) + "\n"


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _text_of(el: Locator) -> str:
    text = el.inner_text().strip()
    if not text:
        text = (el.get_attribute("value") or el.get_attribute("aria-label") or "").strip()
    return re.sub(r"\s+", " ", text)


def matches(text: str, include: list[str], exclude: list[str]) -> bool:
    return any(k in text for k in include) and not any(k in text for k in exclude)


def find_buttons(page: Page, include: list[str], exclude: list[str]) -> list[tuple[Locator, str]]:
    found = []
    for el in page.locator(CLICKABLE).all():
        try:
            if not el.is_visible() or not el.is_enabled():
                continue
            text = _text_of(el)
        except PlaywrightError:
            continue
        if matches(text, include, exclude):
            found.append((el, text))
    return found


def _first_visible(page: Page, selectors: list[str]) -> Locator | None:
    for sel in selectors:
        loc = page.locator(sel)
        for i in range(loc.count()):
            if loc.nth(i).is_visible():
                return loc.nth(i)
    return None


def login(page: Page, cfg: dict, login_id: str, password: str, wait_ms: int) -> None:
    lc = cfg["login"]
    page.goto(lc["url"], wait_until="domcontentloaded")
    pw = _first_visible(page, lc["password_selectors"])
    if pw is None:
        for text in lc.get("open_form_texts", []):
            link = page.get_by_text(text, exact=True).first
            if link.count() and link.is_visible():
                link.click()
                page.wait_for_load_state("domcontentloaded")
                break
        pw = _first_visible(page, lc["password_selectors"])
    if pw is None:
        raise LoginError("パスワード入力欄が見つかりません（config.yaml の login を確認してください）")

    uid = _first_visible(page, lc["id_selectors"])
    if uid is None:
        raise LoginError("ID入力欄が見つかりません（config.yaml の login を確認してください）")
    uid.fill(login_id)
    pw.fill(password)
    pw.press("Enter")
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeout:
        pass
    page.wait_for_timeout(wait_ms)
    if _first_visible(page, lc["password_selectors"]) is not None:
        raise LoginError("ログイン後もログインフォームが表示されています（ID/パスワードを確認してください）")


def discover_links(page: Page, cfg: dict) -> list[str]:
    urls: list[str] = []
    for d in cfg["campaigns"].get("discover", []):
        pattern = re.compile(d["link_pattern"])
        base_host = urlparse(d["page"]).hostname or ""
        try:
            page.goto(d["page"], wait_until="domcontentloaded")
        except PlaywrightError as e:
            print(f"[warn] 一覧ページを開けません: {d['page']} {e}", flush=True)
            continue
        found = []
        for href in page.locator("a[href]").evaluate_all("els => els.map(e => e.getAttribute('href'))"):
            url = urljoin(page.url, href).split("#")[0]
            host = urlparse(url).hostname or ""
            same_site = host == base_host or host.endswith("." + base_host.removeprefix("www."))
            if same_site and pattern.search(url) and url not in found:
                found.append(url)
        urls.extend(found[: d.get("max_links", 20)])
    return urls


def click_all(page: Page, url: str, action: str, include: list[str], exclude: list[str],
              done: list[str], max_clicks: int, wait_ms: int, dry_run: bool, report: Report) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded")
    except PlaywrightError as e:
        report.add(url, action, "error", f"ページを開けません: {e}")
        return

    clicked: set[str] = set()
    for _ in range(max_clicks):
        candidates = [(el, t) for el, t in find_buttons(page, include, exclude) if t not in clicked]
        if not candidates:
            break
        el, text = candidates[0]
        clicked.add(text)
        if dry_run:
            report.add(url, action, "dry-run", f"押す予定: {text}")
            continue
        try:
            el.click()
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PlaywrightTimeout:
                pass
            page.wait_for_timeout(wait_ms)
        except PlaywrightError as e:
            report.add(url, action, "error", f"「{text}」を押せません: {e}")
            continue
        body = page.locator("body").inner_text()
        confirmed = any(k in body for k in done)
        report.add(url, action, "clicked", f"「{text}」を押しました" + ("（完了を確認）" if confirmed else ""))
        if page.url.split("#")[0] != url.split("#")[0]:
            page.goto(url, wait_until="domcontentloaded")

    if not clicked:
        body = page.locator("body").inner_text()
        if any(k in body for k in done):
            report.add(url, action, "already", "参加済み")
        else:
            report.add(url, action, "none", "押せるボタンなし")


def run(cfg: dict, login_id: str, password: str, *, headless: bool = True, dry_run: bool = False,
        artifacts_dir: Path | None = None) -> Report:
    report = Report()
    wait_ms = cfg.get("wait_ms", 1500)

    with sync_playwright() as p:
        # CHROMIUM_PATH があればそのブラウザを使う（ローカルで既存の Chromium を使う場合など）
        browser = p.chromium.launch(headless=headless,
                                    executable_path=os.environ.get("CHROMIUM_PATH") or None)
        context = browser.new_context(locale="ja-JP", timezone_id="Asia/Tokyo")
        context.set_default_timeout(cfg.get("timeout_ms", 30000))
        page = context.new_page()
        page.on("dialog", lambda d: d.accept())
        # キャンペーンが別タブを開いても元のページで処理を続ける
        context.on("page", lambda popup: popup.close() if popup != page else None)

        try:
            login(page, cfg, login_id, password, wait_ms)
            report.logged_in = True
            print("[ok] ログインしました", flush=True)

            lb = cfg.get("login_bonus", {})
            for url in lb.get("urls", []):
                click_all(page, url, "ログインボーナス", lb.get("button_texts", []), [],
                          [], 3, wait_ms, dry_run, report)

            cc = cfg["campaigns"]
            urls = list(dict.fromkeys(cc.get("urls", []) + discover_links(page, cfg)))
            print(f"[info] キャンペーンページ {len(urls)} 件", flush=True)
            for url in urls:
                click_all(page, url, "エントリー", cc["button_texts"], cc.get("exclude_texts", []),
                          cc.get("done_texts", []), cc.get("max_clicks_per_page", 5),
                          wait_ms, dry_run, report)
        except LoginError as e:
            report.add(page.url, "ログイン", "error", str(e))
        except PlaywrightError as e:
            report.add(page.url, "実行", "error", str(e))
        finally:
            if artifacts_dir is not None:
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                try:
                    page.screenshot(path=str(artifacts_dir / "last_page.png"), full_page=True)
                except PlaywrightError:
                    pass
            context.close()
            browser.close()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--dry-run", action="store_true", help="ボタンを押さずに押す予定のものだけ表示する")
    ap.add_argument("--headed", action="store_true", help="ブラウザを表示して実行する")
    ap.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    args = ap.parse_args(argv)

    login_id = os.environ.get("M3_LOGIN_ID")
    password = os.environ.get("M3_PASSWORD")
    if not login_id or not password:
        print("環境変数 M3_LOGIN_ID と M3_PASSWORD を設定してください", file=sys.stderr)
        return 2

    report = run(load_config(args.config), login_id, password, headless=not args.headed,
                 dry_run=args.dry_run, artifacts_dir=args.artifacts)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report.markdown())
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
