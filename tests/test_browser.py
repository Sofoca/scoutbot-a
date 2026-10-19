"""Real-browser regression checks against a local WBM-shaped server; no real applications.

Run with SCOUTBOT_BROWSER_TESTS=1. Chrome and its matching driver must be available.
"""
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wbmbot.application_manager import ApplicationManager, ApplicationError
from wbmbot.run_state import RunState
from wbmbot.scraper import FlatScraper

pytestmark = pytest.mark.skipif(os.getenv("SCOUTBOT_BROWSER_TESTS") != "1", reason="Opt-in real Chrome tests")


@pytest.fixture(scope="module")
def browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if os.getenv("CHROME_BINARY"):
        options.binary_location = os.environ["CHROME_BINARY"]
    with webdriver.Chrome(options=options) as driver:
        driver.set_page_load_timeout(10)
        yield driver


@pytest.fixture
def site():
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_html(self, html):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())

        def do_GET(self):
            if self.path.startswith('/wohnungen-berlin/angebote/vielen-dank') or self.path == '/unrelated/vielen-dank/':
                text = 'Still processing' if 'empty=1' in self.path else 'Wir haben Ihre Anfrage für das Wohnungsangebot erhalten.'
                self.send_html(f'<main><h1>Vielen Dank</h1><p>{text}</p></main>')
                return
            if self.path == "/listings":
                self.send_html('<div class="openimmo-no-offer-avilable__header">No offers available</div>')
                return
            tag = "a" if "legacy" in self.path else "button"
            # Current WBM uses a button with data-target; old code required an anchor with scrollLink.
            control = (f'<{tag} type="button" class="btn extra openimmo-detail__contact-box-button" '
                       'data-target=".tx-powermail" onclick="document.getElementById(\'contact\').style.display=\'block\'">'
                       f'Anfrage absenden</{tag}>')
            target = self.path if 'redirect' in self.path else "/error" if "error" in self.path else "/unknown" if "unknown" in self.path else "/success"
            self.send_html(control + f'''<div style="height:100px"></div>
                <form id="contact" class="powermail_form" action="{target}" method="post" style="display:none">
                <input id="powermail_field_name" name="name" required>
                <input id="powermail_field_vorname" name="vorname" required>
                <input id="powermail_field_e_mail" name="email" required>
                <input type="checkbox" id="powermail_field_datenschutzhinweis_1" name="consent" required>
                <label for="powermail_field_datenschutzhinweis_1">Consent</label>
                <button class="extra btn-primary btn" type="submit">Anfrage absenden</button></form>''')

        def do_POST(self):
            posts.append(parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()))
            if 'redirect' in self.path:
                destination = '/unrelated/vielen-dank/' if 'wrong' in self.path else '/wohnungen-berlin/angebote/vielen-dank/'
                if 'empty' in self.path:
                    destination += '?empty=1'
                self.send_response(303)
                self.send_header('Location', destination)
                self.end_headers()
                return
            if self.path == "/error":
                self.send_html('<div class="powermail_message_error">Form rejected</div><div class="powermail_create">Result</div>')
            elif self.path == "/unknown":
                self.send_html('<p>Still processing</p>')
            else:
                self.send_html('<div class="powermail_create">Vielen Dank für Ihre Anfrage.</div>')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", posts
    server.shutdown()
    server.server_close()
    thread.join()


def make_manager(browser, tmp_path, monkeypatch):
    user = SimpleNamespace(first_name="Test", last_name="Applicant", email="test@example.invalid")
    manager = ApplicationManager(browser, user, tmp_path / "flats.log", RunState(tmp_path / "state.json"))
    manager.wait = WebDriverWait(browser, 0.5)
    monkeypatch.setattr(manager, "_dismiss_cookie_banner", lambda: None)
    return manager


@pytest.mark.parametrize("page", ["current", "legacy", "redirect"])
def test_current_and_legacy_form_confirm_once(browser, site, tmp_path, monkeypatch, page):
    url, posts = site
    browser.get(f"{url}/{page}")
    manager = make_manager(browser, tmp_path, monkeypatch)
    flat = SimpleNamespace(hash="testhash", title="Test flat", detail_link=f"{url}/{page}")
    handler = logging.FileHandler(manager.log_path)
    logger = logging.getLogger("flats")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        assert manager.apply(flat)
        assert len(posts) == 1
        assert posts[0] == {"name": ["Applicant"], "vorname": ["Test"], "email": ["test@example.invalid"], "consent": ["on"]}
        assert manager.has_applied(flat.hash)
        assert not manager.apply(flat)
        assert len(posts) == 1
        assert not manager.state.data["pending"]
        assert "Applicant" not in manager.log_path.read_text()
        assert "Test flat" not in manager.log_path.read_text()
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)


def test_old_selector_reproduces_failure(browser, site):
    url, _ = site
    browser.get(f"{url}/current")
    with pytest.raises(TimeoutException):
        WebDriverWait(browser, 0).until(EC.element_to_be_clickable((By.XPATH,
            '//a[@class="openimmo-detail__contact-box-button btn scrollLink"]')))
    assert browser.find_element(By.CSS_SELECTOR, '.openimmo-detail__contact-box-button').tag_name == "button"


@pytest.mark.parametrize("page, error", [("error", ApplicationError), ("unknown", TimeoutException),
                                        ("redirect-wrong", TimeoutException), ("redirect-empty", TimeoutException)])
def test_rejection_or_unknown_result_is_not_logged_or_reposted(browser, site, tmp_path, monkeypatch, page, error):
    url, posts = site
    browser.get(f"{url}/{page}")
    manager = make_manager(browser, tmp_path, monkeypatch)
    flat = SimpleNamespace(hash="testhash", title="Test flat", detail_link=f"{url}/{page}")
    with pytest.raises(error):
        manager.apply(flat)
    assert not manager.has_applied(flat.hash)
    assert len(posts) == 1
    with pytest.raises(ApplicationError, match="earlier submission"):
        manager.apply(flat)
    assert len(posts) == 1


def test_listing_page_needs_no_footer(browser, site, monkeypatch):
    url, _ = site
    scraper = FlatScraper(browser, f"{url}/listings")
    monkeypatch.setattr(scraper, "_dismiss_cookie_banner", lambda: None)
    scraper.load_start_page()
    assert scraper.get_flats() == []
