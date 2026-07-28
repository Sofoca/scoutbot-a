import logging
import time

from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeout
from .flat import Flat

logger = logging.getLogger("app")

# Page load can hang; Selenium client then raises urllib3 ReadTimeoutError unwrapped
_LOAD_ERRORS = (TimeoutException, WebDriverException, TimeoutError, Urllib3ReadTimeout)


class FlatScraper:
    def __init__(self, driver, start_url):
        self.driver = driver
        self.start_url = start_url
        self.wait = WebDriverWait(self.driver, 10)

    def load_start_page(self, retries=3):
        self._get_with_retry(self.start_url, retries=retries)
        self._scroll_to_footer()
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

    def get_details(self, detail_link):
        # Navigate to details page and extract details from detail page
        self._get_with_retry(detail_link)
        detail_attrs = self._extract_detail_attributes()
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

    def _scroll_to_footer(self):
        footer = self.wait.until(EC.visibility_of_element_located((By.TAG_NAME, 'footer')))
        ActionChains(self.driver).scroll_to_element(footer).perform()

    def _extract_summary_attributes(self, flat_elem):
        # Extract title, total rent, size, rooms, zip_code, property attributes summary from flat_elem, link to detail page
        self.wait.until(EC.presence_of_element_located((By.XPATH, './/*[@title="Details"]')))
        title = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'imageTitle')
        total_rent = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-rent')
        size = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-size')
        rooms = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'main-property-rooms')
        zip_code = self._find_element_text_safe(flat_elem, By.CLASS_NAME, 'address')
        property_attrs_elems = flat_elem.find_elements(By.XPATH, './/ul[@class="check-property-list"]/li')
        property_attrs = [elem.text for elem in property_attrs_elems] # empty list if none found
        detail_link = self._find_element_attr_safe(flat_elem, By.XPATH, './/*[@title="Details"]', attr='href')

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
        base_rent = self._find_element_text_safe(self.driver, By.CLASS_NAME, 'openimmo-detail__rental-costs-list-item-value')
        return {
            "base_rent": base_rent,
            # maybe add floor here
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


