import time
import logging
from pathlib import Path
from urllib.parse import urlsplit
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, ElementClickInterceptedException, ElementNotInteractableException,
)
from .scraper import ListingUnavailableError

logger_app = logging.getLogger("app")
logger_flats = logging.getLogger("flats")

class ApplicationError(RuntimeError):
    """Actionable application failure, with the stage included in its message."""


class ApplicationManager:
    def __init__(self, driver, user, log_path="logs/flats.log", state=None):
        self.driver = driver
        self.user = user
        self.log_path = Path(log_path)
        self.wait = WebDriverWait(self.driver, 10)
        self.state = state

    def has_applied(self, flat_hash):
        if not self.log_path.exists():
            return False
        with self.log_path.open("r", encoding="utf-8") as logfile:
            return flat_hash in logfile.read()

    def apply(self, flat):
        if self.has_applied(flat.hash):
            logger_app.info(f"Already applied for flat: {flat.title}")
            return False

        if self.state and flat.hash in self.state.data["pending"]:
            raise ApplicationError(
                "An earlier submission has no confirmed result. Check the WBM confirmation "
                "email before retrying; automatic resubmission is paused to avoid duplicates. "
                "After checking, remove this flat's entry from logs/state.json on the log branch."
            )

        try:
            self._fill_form_and_submit(flat)
        except ListingUnavailableError:
            logger_app.info(f"Flat '{flat.title}' is no longer available; skipping: {flat.detail_link}")
            return False
        self._log_application(flat)
        if self.state:
            self.state.clear_pending(flat)
        logger_app.info(f"Application submitted for: {flat.title}")
        return True

    def _dismiss_cookie_banner(self):
        try:
            WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, '.cn-buttons'))
            )
        except TimeoutException:
            return
        self.driver.execute_script("""
            var btn = document.querySelector('.cn-accept-cookie, .cn-decline, .cn-ok');
            if (btn) { btn.click(); }
            ['cookie-notice', 'cn-wrapper'].forEach(function(id) {
                var el = document.getElementById(id);
                if (el) { el.remove(); }
            });
            document.querySelectorAll('.cookie-notice, [class*="cookie-notice"]').forEach(function(el) {
                el.remove();
            });
            var cnButtons = document.querySelector('.cn-buttons');
            if (cnButtons && cnButtons.parentElement) {
                cnButtons.parentElement.remove();
            }
        """)
        time.sleep(0.3)
        logger_app.info("Cookie consent banner dismissed")

    def _js_click(self, element):
        try:
            element.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            self.driver.execute_script("arguments[0].click();", element)

    def _wait_for(self, locator, stage, clickable=False):
        condition = EC.element_to_be_clickable if clickable else EC.visibility_of_element_located
        return self.wait.until(condition(locator), message=f"Application failed while {stage}")

    def _is_unavailable(self):
        try:
            current_url = getattr(self.driver, "current_url", "") or ""
            if any(term in current_url.lower() for term in ("nicht-mehr-verfuegbar", "not-available")):
                return True
            title = (getattr(self.driver, "title", "") or "").lower()
            if any(term in title for term in ("nicht mehr verfügbar", "not available")) or title.startswith("404"):
                return True
            headers = self.driver.find_elements(
                By.CSS_SELECTOR, "h1, h2, .m-text__header, .openimmo-no-offer-avilable__header"
            )
            for h in headers:
                try:
                    h_text = h.text.lower()
                    if "nicht mehr verfügbar" in h_text or "not available" in h_text:
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def _prepare_form(self):
        self._dismiss_cookie_banner()
        if self._is_unavailable():
            raise ListingUnavailableError("Flat is no longer available; contact form cannot be opened")
        # WBM now uses a button; older pages used an anchor with extra classes.
        contact = self._wait_for(
            (By.CSS_SELECTOR, '.openimmo-detail__contact-box-button'),
            "opening the contact form (WBM may have changed its page)", clickable=True,
        )
        self._js_click(contact)
        for field_id, value in (
            ("name", self.user.last_name), ("vorname", self.user.first_name),
            ("e_mail", self.user.email),
        ):
            field = self._wait_for((By.ID, f"powermail_field_{field_id}"), f"finding the {field_id} field")
            field.clear()
            field.send_keys(value)
        checkbox = self.driver.find_element(By.ID, "powermail_field_datenschutzhinweis_1")
        if not checkbox.is_selected():
            self._js_click(self._wait_for(
                (By.CSS_SELECTOR, 'label[for="powermail_field_datenschutzhinweis_1"]'),
                "selecting privacy consent", clickable=True,
            ))
        if not checkbox.is_selected():
            raise ApplicationError("Privacy consent could not be selected; application was not submitted")
        return self._wait_for(
            (By.CSS_SELECTOR, 'form.powermail_form button[type="submit"]'),
            "finding the submit button", clickable=True,
        )

    def _fill_form_and_submit(self, flat):
        # Preparation can be retried safely. Never repeat a click after a POST timeout.
        for attempt in range(2):
            try:
                submit_btn = self._prepare_form()
                break
            except ListingUnavailableError:
                raise
            except (TimeoutException, ElementClickInterceptedException, ElementNotInteractableException):
                if attempt:
                    raise
                logger_app.warning("Contact form preparation failed; retrying once")

        invalid = self.driver.execute_script("""
            return Array.from(arguments[0].form.querySelectorAll(':invalid'))
                .map(el => el.id || el.name);
        """, submit_btn)
        if invalid:
            raise ApplicationError(f"WBM form validation failed before submission: {', '.join(invalid)}")
        if self.state:
            self.state.mark_pending(flat)
        self._submission_origin = self.driver.execute_script("return location.origin;")
        self.driver.set_page_load_timeout(45)
        try:
            self._js_click(submit_btn)
        except TimeoutException:
            logger_app.warning("Submission navigation timed out; checking WBM confirmation without resubmitting")
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass
        try:
            self.wait.until(self._submission_confirmed, message=(
                "WBM did not show a submission confirmation. Outcome unknown."
            ))
        except TimeoutException:
            # Never save the filled page, query string, form values, or browser logs.
            path = urlsplit(self.driver.current_url).path
            form_present = bool(self.driver.find_elements(By.CSS_SELECTOR, 'form.powermail_form'))
            logger_app.warning("Unconfirmed submission: response path=%s, application form present=%s", path, form_present)
            raise TimeoutException(
                f"WBM did not show a recognized confirmation. Response page: {path}; "
                f"application form still present: {form_present}. Outcome unknown."
            ) from None

    def _submission_confirmed(self, driver):
        # Inspect one DOM snapshot: navigation between separate error/success queries
        # can otherwise misclassify an error page that also has a create wrapper.
        result = driver.execute_script(r"""
            const visible = el => el.getClientRects().length > 0 &&
                getComputedStyle(el).visibility !== 'hidden' && el.textContent.trim();
            if (Array.from(document.querySelectorAll('.powermail_message_error, .powermail-errors-list'))
                .some(visible)) return 'error';
            if (Array.from(document.querySelectorAll('.powermail_create')).some(visible)) return 'confirmed';
            const main = document.querySelector('main');
            const text = main?.innerText.replace(/\s+/g, ' ').trim() || '';
            if (location.origin === arguments[0] &&
                location.pathname.replace(/\/$/, '') === '/wohnungen-berlin/angebote/vielen-dank' &&
                text.includes('Wir haben Ihre Anfrage für das Wohnungsangebot erhalten.')) return 'confirmed';
            return null;
        """, self._submission_origin)
        if result == "error":
            raise ApplicationError("WBM reported a form error; check the application form and its required fields")
        return result == "confirmed"

    def _log_application(self, flat):
        # Only the hash is needed for deduplication in a public repository.
        logger_flats.info("Application confirmed; hash: %s", flat.hash)
