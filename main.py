import logging
import hashlib
import os
import time
from pathlib import Path

import requests
from selenium import webdriver
from selenium.common.exceptions import WebDriverException
from selenium.webdriver.chrome.options import Options
from urllib3.exceptions import ReadTimeoutError as Urllib3ReadTimeout

from wbmbot import User, ConfigLoader, FlatScraper, ApplicationManager
from wbmbot.application_manager import ApplicationError
from wbmbot.run_state import RunState
from wbmbot.utils import setup_loggers

logger = logging.getLogger("app")
TRANSIENT_ERRORS = (WebDriverException, TimeoutError, Urllib3ReadTimeout)


class NotificationDeliveryError(RuntimeError):
    pass


def send_telegram(text):
    """Send only to the configured channel; never log a token-bearing request URL."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    for attempt in range(3):
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text[:4000]}, timeout=10,
            )
            if response.ok and response.json().get("ok") is True:
                return True
            if response.status_code < 500 and response.status_code != 429:
                break
        except (requests.RequestException, ValueError):
            pass
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    logger.warning("Telegram delivery failed after retries")
    return False


def notify_issue(state, key, message):
    # Repeat only when an incident changes or recurs after recovery.
    fingerprint = hashlib.sha256(message.encode()).hexdigest()
    if state and state.data["incidents"].get(key) == fingerprint:
        return
    Path("logs").mkdir(exist_ok=True)
    Path("logs/attention").touch()
    repository = os.getenv("GITHUB_REPOSITORY", "local scoutbot")
    run_id = os.getenv("GITHUB_RUN_ID")
    text = f"⚠️ {repository}\n{message}"
    if run_id:
        text += f"\nLogs: https://github.com/{repository}/actions/runs/{run_id}"
    Path("logs/alert.txt").write_text(text)
    if not send_telegram(text):
        raise NotificationDeliveryError("Actionable failure could not be delivered to Telegram; email fallback required")
    if state:
        state.data["incidents"][key] = fingerprint
        state.save()


def error_detail(error):
    detail = str(error).split("Stacktrace")[0].removeprefix("Message:").strip()
    return detail[:500] or f"{type(error).__name__}: browser operation did not complete"


def main(state):
    user = User(ConfigLoader(config_var="USER_CONFIG").load_user_data())
    options = Options()
    if os.getenv("CHROME_BINARY"):
        options.binary_location = os.environ["CHROME_BINARY"]
    options.page_load_strategy = "eager"
    for argument in (
        "--disable-extensions", "--disable-gpu", "--headless=new", "--no-first-run",
        "--no-default-browser-check", "--no-sandbox", "--disable-dev-shm-usage",
        "--disable-background-networking", "--log-level=3",
    ):
        options.add_argument(argument)

    with webdriver.Chrome(options=options) as driver:
        driver.set_page_load_timeout(45)
        scraper = FlatScraper(driver, "https://www.wbm.de/wohnungen-berlin/angebote/")
        app_manager = ApplicationManager(driver, user, state=state)
        scraper.load_start_page()
        flats = scraper.get_flats()
        state.query_succeeded()
        for flat in flats:
            if not flat.matches_criteria(user):
                logger.info("Flat '%s' does not meet search criteria; skipping", flat.title)
                continue
            stage = "loading flat details"
            try:
                if app_manager.has_applied(flat.hash):
                    continue
                if flat.hash in state.data["pending"] and flat.hash in state.data["incidents"]:
                    logger.warning("Previously reported uncertain submission remains paused: %s", flat.detail_link)
                    continue
                flat.update_details(scraper.get_details(flat.detail_link))
                if flat.base_rent is None:
                    raise ApplicationError("WBM base rent could not be read; eligibility could not be checked")
                if not flat.within_range(user):
                    continue
                stage = "preparing or submitting the application"
                logger.info("Flat %s matches criteria; applying", flat.title)
                if app_manager.apply(flat):
                    message = [
                        f"{user.first_name} applied to a flat! 🎉", flat.title,
                        f"📍 {flat.zip_code} | {flat.size}m² | {flat.rooms} rooms",
                        f"💰 Total rent: {flat.total_rent}€ | Base rent: {flat.base_rent}€",
                        "🔴 WBS required" if flat.wbs else "✅ No WBS required",
                        f"🔗 {flat.detail_link}",
                    ]
                    if flat.property_attrs:
                        message.append(f"🏠 {', '.join(flat.property_attrs)}")
                    send_telegram("\n".join(message))
            except (ApplicationError, *TRANSIENT_ERRORS) as error:
                logger.exception("Application problem while %s: %s", stage, flat.detail_link)
                reason = error_detail(error)
                if flat.hash in state.data["pending"]:
                    reason += (
                        "\nCheck the WBM confirmation email before retrying. "
                        "Automatic resubmission is paused to avoid duplicates; after checking, "
                        "remove this flat's pending entry in logs/state.json on the log branch."
                    )
                notify_issue(state, flat.hash,
                    f"Application needs attention: {flat.title}\nStage: {stage}\n{reason}\n{flat.detail_link}\nReference: {flat.hash}")


def run():
    setup_loggers()
    state = None
    try:
        state = RunState()
        main(state)
        state.data["incidents"].pop("run", None)
        state.save()
    except NotificationDeliveryError:
        logger.exception("Telegram unavailable; failing the workflow for email fallback")
        raise
    except TRANSIENT_ERRORS as error:
        logger.exception("Query/browser failed after local retries")
        if state is None or state.query_failed() >= 3:
            notify_issue(state, "query",
                "Flat queries have failed for at least 3 consecutive runs after retries. "
                "The bot cannot search or apply while this persists.\n" + error_detail(error))
    except Exception as error:
        logger.exception("Non-transient run failure")
        notify_issue(state, "run", "Scoutbot cannot complete its run.\n" + error_detail(error))


if __name__ == "__main__":
    run()
