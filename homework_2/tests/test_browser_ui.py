"""Exercise the served HTML, JavaScript and CSS against a real local server."""
import os
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import URLError
from urllib.request import urlopen

import pytest

pw = pytest.importorskip("playwright.sync_api")
PROJECT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    with pw.sync_playwright() as runner:
        executable = os.getenv("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
        try:
            chromium = runner.chromium.launch(executable_path=executable)
        except pw.Error as error:
            if "executable doesn't exist" in str(error).lower():
                pytest.skip("Install the browser with python -m playwright install chromium")
            raise
        yield chromium
        chromium.close()


@pytest.fixture
def page(browser, tmp_path, request):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    database = tmp_path / "news.db"
    if getattr(request, "param", None) == "demo":
        from scripts.prepare_demo import prepare_demo
        prepare_demo(database, url)
        prepare_demo(database, url)  # A second launch must preserve the demo.
    env = {**os.environ, "RSS_DB_PATH": str(database), "HF_HUB_OFFLINE": "1",
           "RSS_POLL_SECONDS": "3600"}
    with (tmp_path / "uvicorn.log").open("w+") as log:
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
             "--port", str(port)], cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and server.poll() is None:
                try:
                    with urlopen(url + "/api/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except URLError:
                    time.sleep(0.1)
            else:
                log.seek(0)
                pytest.fail(f"Uvicorn did not start:\n{log.read()}")

            context = browser.new_context(base_url=url, viewport={"width": 1440, "height": 960})
            tab = context.new_page()
            errors = []
            tab.on("pageerror", lambda error: errors.append(str(error)))
            try:
                tab.goto("/")
                yield tab
                assert not errors, errors
            finally:
                context.close()
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


def add_user(page, name):
    page.locator("#addUserBtn").click()
    page.locator("#simpleForm [name=name]").fill(name)
    page.locator("#simpleForm [type=submit]").click()
    pw.expect(page.locator("#simpleDialog")).to_be_hidden()
    pw.expect(page.locator("#userHint")).to_contain_text(name)
    return page.locator("#userSelect").input_value()


def save_keyword(page, name, word, *, edit=False):
    page.locator("[data-view=filters]").click()
    page.locator("[data-edit-filter]" if edit else "#newFilterBtn").click()
    page.locator("#filterForm [name=name]").fill(name)
    page.locator("#filterForm [name=any]").fill(word)
    page.locator("#filterForm [type=submit]").click()
    pw.expect(page.locator("#filterDialog")).to_be_hidden()
    pw.expect(page.locator(".filter-card h3")).to_have_text(name)


def test_two_profiles_publish_edit_and_live_update(page):
    pw.expect(page.locator("#newsList")).to_contain_text("Создайте пользователя")
    pw.expect(page.locator("#filtersView")).to_be_hidden()
    anna = add_user(page, "Аня")
    save_keyword(page, "Нефть", "нефть")
    page.locator("[data-view=channel]").click()
    page.locator("#channelForm [name=title]").fill("Канал семинара")
    page.locator("#channelForm [type=submit]").click()
    title = page.locator("#postForm [name=title]")
    title.fill("Нефть подорожала")
    page.locator("#postForm [name=text]").fill("Обзор рынка нефти")
    page.locator("#postForm [type=submit]").click()
    pw.expect(title).to_have_value("")
    page.locator("[data-view=news]").click()
    cards = page.locator(".news-card")
    pw.expect(cards).to_have_count(1)
    pw.expect(cards.locator(".reason")).to_contain_text("нефть", ignore_case=True)
    pw.expect(cards).not_to_contain_text("[object Object]")
    pw.expect(page.locator("#connectionText")).to_contain_text("онлайн")

    channel = page.request.get("/api/channels").json()[0]
    # Publish outside this tab. Only the live stream can update the open view.
    response = page.request.post(f"/api/channels/{channel['id']}/articles",
                                 data={"title": "Нефть на торгах", "text": "Объём сделок вырос"})
    assert response.status == 201, response.text()
    pw.expect(cards).to_have_count(2, timeout=15000)

    add_user(page, "Борис")
    page.locator("[data-view=feeds]").click()
    page.locator("[data-subscribe]").click()
    pw.expect(page.locator("#feedsList")).to_contain_text("Канал семинара")
    save_keyword(page, "Банки", "банк")
    page.locator("[data-view=news]").click()
    pw.expect(page.locator("#newsList")).to_contain_text("Пока нет совпадений")
    save_keyword(page, "Энергетика", "нефть", edit=True)
    page.locator("[data-view=news]").click()
    pw.expect(cards).to_have_count(2)
    pw.expect(cards.first).to_contain_text("Энергетика")
    page.locator("#userSelect").select_option(anna)
    pw.expect(cards.first.locator(".match-badge")).to_contain_text("Нефть")
    page.locator("[data-view=channel]").click()
    pw.expect(page.locator("#channelTitle")).to_have_text("Канал семинара")


def test_subscribe_and_unsubscribe_external_rss(page):
    class Feed(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml; charset=utf-8")
            self.end_headers()
            self.wfile.write("""<rss version="2.0"><channel><title>Архив семинара</title>
                <item><guid isPermaLink="false">42</guid><title>Нефть на рынке</title>
                <description>Котировки за неделю.</description></item></channel></rss>""".encode())

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Feed) as rss:
        thread = Thread(target=rss.serve_forever, daemon=True)
        thread.start()
        try:
            add_user(page, "Катя")
            save_keyword(page, "Энергетика", "нефть")
            page.locator("[data-view=feeds]").click()
            page.locator("#addFeedBtn").click()
            page.locator("#simpleForm [name=url]").fill(f"http://127.0.0.1:{rss.server_port}/rss")
            page.locator("#simpleForm [type=submit]").click()
            pw.expect(page.locator("#feedsList")).to_contain_text("Архив семинара")
            page.locator("[data-view=news]").click()
            pw.expect(page.locator(".news-card")).to_contain_text("Нефть на рынке")
            page.locator("[data-view=feeds]").click()
            page.locator("[data-unsubscribe]").click()
            pw.expect(page.locator("#catalogFeeds")).to_contain_text("Архив семинара")
            page.locator("[data-view=news]").click()
            pw.expect(page.locator(".news-card")).to_have_count(0)
        finally:
            rss.shutdown()
            thread.join()


@pytest.mark.skipif(os.getenv("RUN_MODEL_TESTS") != "1", reason="Requires the cached semantic model")
@pytest.mark.parametrize("page", ["demo"], indirect=True)
def test_demo_shows_real_semantic_match_and_owns_channel(page):
    cards = page.locator(".news-card")
    policy = cards.filter(has_text="Президент провёл переговоры")
    pw.expect(policy.locator(".match-badge.topic")).to_contain_text("Политика", timeout=90000)
    pw.expect(policy.locator(".reason")).to_contain_text("0.45")
    page.locator("[data-filter-view=topic]").click()
    pw.expect(policy).to_be_visible()
    page.locator("[data-view=channel]").click()
    pw.expect(page.locator("#channelTitle")).to_have_text("Учебная редакция")
    page.locator("#userSelect").select_option(label="Борис")
    pw.expect(page.locator("#postPanel")).to_be_hidden()
    page.locator("[data-view=news]").click()
    page.locator("[data-filter-view=all]").click()
    pw.expect(cards).to_have_count(1)
    pw.expect(cards).to_contain_text("Банк открыл отделение")
