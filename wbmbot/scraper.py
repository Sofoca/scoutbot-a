import logging
import time

import re
from urllib.parse import urljoin
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeout
from .flat import Flat

logger = logging.getLogger("app")

# Page load can hang; Selenium client then raises urllib3 ReadTimeoutError unwrapped
_LOAD_ERRORS = (TimeoutException, WebDriverException, TimeoutError, Urllib3ReadTimeout)


class ListingUnavailableError(Exception):
    """Raised when a flat listing is no longer available on WBM."""


class FlatScraper:
    def __init__(self, driver, start_url):
        self.driver = driver
        self.start_url = start_url
        self.wait = WebDriverWait(self.driver, 10)

    def load_start_page(self, retries=3):
        self._get_with_retry(self.start_url, retries=retries)
        self.wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR,
                '.openimmo-search-list-item, .openimmo-no-offer-avilable__header')),
            message=f"Listing results or the explicit no-offers notice did not load: {self.start_url}",
        )
        return self.driver.page_source  # for testing

    def get_flats(self):
        logger.info("Searching flats...")
        flat_elements = self.driver.find_elements(By.CSS_SELECTOR, ".row.openimmo-search-list-item")
        if not flat_elements:
            logger.info("No flats available.")
        flats = []
        for elem in flat_elements:
            # Extract summary attributes
            summary_attrs = self._extract_summary_attributes(elem)
            flat = Flat(summary_attrs)
            flats.append(flat)

        return flats

    def is_listing_available(self):
        try:
            current_url = getattr(self.driver, "current_url", "") or ""
            if any(term in current_url.lower() for term in ("nicht-mehr-verfuegbar", "not-available")):
                return False

            title = (getattr(self.driver, "title", "") or "").lower()
            if any(term in title for term in ("nicht mehr verfügbar", "not available")) or title.startswith("404"):
                return False

            headers = self.driver.find_elements(
                By.CSS_SELECTOR, "h1, h2, .m-text__header, .openimmo-no-offer-avilable__header"
            )
            for h in headers:
                try:
                    h_text = h.text.lower()
                    if "nicht mehr verfügbar" in h_text or "not available" in h_text:
                        return False
                except Exception:
                    pass

            detail_containers = self.driver.find_elements(
                By.CSS_SELECTOR,
                ".openimmo-detail, .openimmo-detail__rental-costs-container, .openimmo-detail__contact-box-button"
            )
            if not detail_containers:
                body_text = self._find_element_text_safe(self.driver, By.TAG_NAME, "body").lower()
                if "nicht mehr verfügbar" in body_text or "angebot nicht mehr verfügbar" in body_text:
                    return False
        except Exception as e:
            logger.warning(f"Error checking listing availability: {e}")
        return True

    def get_details(self, detail_link):
        # Navigate to details page and extract details from detail page
        self._get_with_retry(detail_link)
        if not self.is_listing_available():
            logger.info(f"Listing is no longer available: {detail_link}")
            return {
                "available": False,
                "base_rent": None,
                "detail_url": detail_link,
            }
        detail_attrs = self._extract_detail_attributes()
        detail_attrs["available"] = True
        detail_attrs["detail_url"] = detail_link
        return detail_attrs

    def _get_with_retry(self, url, retries=3):
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                logger.info(f"Connecting to {url} (attempt {attempt}/{retries})")
                self.driver.get(url)
                self._dismiss_cookie_banner()
                return
            except _LOAD_ERRORS as e:
                last_err = e
                logger.warning(f"Page load failed (attempt {attempt}/{retries}): {e}")
                try:
                    self.driver.execute_script("window.stop();")
                except Exception:
                    pass
                if attempt < retries:
                    time.sleep(2 * attempt)
        raise last_err

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
        logger.info("Cookie consent banner dismissed")

    def _extract_summary_attributes(self, flat_elem):
        # Extract title, total rent, size, rooms, zip_code, property attributes summary from flat_elem, link to detail page
        WebDriverWait(flat_elem, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'a[title="Details"]')),
            message="Listing card is missing its Details link",
        )
        title = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'imageTitle')
        total_rent = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-rent')
        size = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-size')
        rooms = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-rooms')
        zip_code = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'address')
        property_attrs_elems = flat_elem.find_elements(By.XPATH, './/ul[@class="check-property-list"]/li')
        property_attrs = [elem.text for elem in property_attrs_elems] # empty list if none found
        detail_link = self._find_element_attr_safe(flat_elem, By.XPATH, './/*[@title="Details"]', attr='href')
        if detail_link and detail_link.startswith("/"):
            detail_link = urljoin("https://www.wbm.de", detail_link)

        return {
            "title": title,
            "total_rent": total_rent,
            "size": size,
            "rooms": rooms,
            "zip_code": zip_code,
            "property_attrs": property_attrs,
            "detail_url": detail_link,
        }

    def _extract_detail_attributes(self):
        # Extract the detail page specific fields like base_rent and others
        base_rent = ""
        cost_items = self.driver.find_elements(By.CSS_SELECTOR, '.openimmo-detail__rental-costs-list-item')
        for item in cost_items:
            title_text = self._find_element_text_safe(
                item, By.CSS_SELECTOR, '.openimmo-detail__rental-costs-list-item-title'
            ).lower()
            if any(k in title_text for k in ("kalt", "grundmiete")):
                val = self._find_element_text_safe(
                    item, By.CSS_SELECTOR, '.openimmo-detail__rental-costs-list-item-value'
                )
                if val:
                    base_rent = val
                    break

        if not base_rent:
            base_rent = self._find_element_text_safe(
                self.driver, By.CLASS_NAME, 'openimmo-detail__rental-costs-list-item-value'
            )

        if not base_rent:
            try:
                page_source = getattr(self.driver, "page_source", "") or ""
                match = re.search(
                    r'(?:nettokaltmiete|kaltmiete)\s*[:]?\s*([0-9.,\s]+)\s*(?:€|eur)',
                    page_source,
                    re.IGNORECASE,
                )
                if match:
                    base_rent = match.group(1).strip()
            except Exception:
                pass

        return {
            "base_rent": base_rent,
        }

    @staticmethod
    def _find_element_text_safe(elem, by, value, fallback=""):
        try:
            return elem.find_element(by, value).text
        except NoSuchElementException:
            return fallback

    @staticmethod
    def _find_element_attr_safe(elem, by, value, attr, fallback=""):
        try:
            return elem.find_element(by, value).get_attribute(attr)
        except NoSuchElementException:
            return fallback


