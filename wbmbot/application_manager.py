import os
import time
import logging
from pathlib import Path
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logger_app = logging.getLogger("app")
logger_flats = logging.getLogger("flats")

class ApplicationManager:
    def __init__(self, driver, user, log_path="logs/flats.log"):
        self.driver = driver
        self.user = user
        self.log_path = Path(log_path)
        self.wait = WebDriverWait(self.driver, 10)

    def has_applied(self, flat_hash):
        with self.log_path.open("r", encoding="utf-8") as logfile:
            return flat_hash in logfile.read()

    def apply(self, flat):
        if self.has_applied(flat.hash):
            logger_app.info(f"Already applied for flat: {flat.title}")
            return False

        self._fill_form_and_submit(flat)
        self._log_application(flat)
        logger_app.info(f"Application submitted for: {flat.title}")
        return True

    def _fill_form_and_submit(self, flat):
        # assumes flat's detail page is already loaded in driver
        scroll_to_form_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//a[@class="openimmo-detail__contact-box-button btn scrollLink"]')))
        scroll_to_form_btn.click()
        time.sleep(1.2)
        name_field = self.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="powermail_field_name"]')))
        name_field.send_keys(self.user.last_name)
        time.sleep(0.5)
        vorname_field = self.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="powermail_field_vorname"]')))
        vorname_field.send_keys(self.user.first_name)
        time.sleep(1)
        email_field = self.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="powermail_field_e_mail"]')))
        email_field.send_keys(self.user.email)
        time.sleep(1.2)
        checkbox = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//label[@for="powermail_field_datenschutzhinweis_1"]')))
        checkbox.click()
        time.sleep(0.5)
        submit_btn = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//button[@class="btn btn-primary" and @type="submit"]')))
        # Form POST starts navigation; confirmation page often hangs under headless.
        # Allow longer load, then treat timeout as success (request usually already sent).
        self.driver.set_page_load_timeout(90)
        try:
            submit_btn.click()
        except TimeoutException:
            logger_app.warning(
                f"Submit navigation timed out for '{flat.title}'; assuming form posted"
            )
            try:
                self.driver.execute_script("window.stop();")
            except Exception:
                pass
        finally:
            self.driver.set_page_load_timeout(45)

    def _log_application(self, flat):
        logger_flats.info(
            f"Application sent:\n"
            f"{flat.title}\n"
            f"zip code: {flat.zip_code}\n"
            f"flat size: {flat.size}\n"
            f"rooms: {flat.rooms}\n"
            f"property attributes: {', '.join(flat.property_attrs)}\n"
            f"hash: {flat.hash}\n"
        )