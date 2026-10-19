import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.support.ui import WebDriverWait

import main
from wbmbot.application_manager import ApplicationManager, ApplicationError
from wbmbot.run_state import RunState
from wbmbot.config_loader import ConfigLoader


@pytest.fixture
def state(tmp_path):
    return RunState(tmp_path / "state.json")


@pytest.fixture
def flat():
    return SimpleNamespace(hash="example-flat-hash", title="Example flat", detail_link="https://www.wbm.de/example")


@pytest.fixture
def manager(tmp_path, state):
    return ApplicationManager(Mock(), SimpleNamespace(first_name="Test", last_name="Applicant", email="test@example.invalid"),
                              tmp_path / "flats.log", state)


def test_no_success_record_without_confirmation(manager, flat, mocker):
    mocker.patch.object(manager, "_prepare_form", return_value=Mock())
    manager.driver.execute_script.return_value = []
    manager.driver.find_elements.return_value = []
    manager.wait = WebDriverWait(manager.driver, 0)
    record = mocker.patch.object(manager, "_log_application")
    with pytest.raises(TimeoutException, match="Submission|submission"):
        manager.apply(flat)
    record.assert_not_called()
    assert RunState(manager.state.path).data["pending"][flat.hash]
    click_count = manager._prepare_form.return_value.click.call_count
    with pytest.raises(ApplicationError, match="earlier submission"):
        manager.apply(flat)
    assert manager._prepare_form.return_value.click.call_count == click_count == 1


def test_navigation_timeout_does_not_repeat_submit(manager, flat, mocker):
    button = Mock()
    button.click.side_effect = TimeoutException("slow POST")
    mocker.patch.object(manager, "_prepare_form", return_value=button)
    manager.driver.execute_script.return_value = []
    mocker.patch.object(manager, "_submission_confirmed", return_value=True)
    record = mocker.patch.object(manager, "_log_application")
    assert manager.apply(flat)
    button.click.assert_called_once()
    record.assert_called_once_with(flat)
    assert not manager.state.data["pending"]


def test_click_fallback_only_for_intercepted_click(manager):
    element = Mock()
    element.click.side_effect = ElementClickInterceptedException()
    manager._js_click(element)
    manager.driver.execute_script.assert_called_once_with("arguments[0].click();", element)
    manager.driver.reset_mock()
    element.click.side_effect = TimeoutException()
    with pytest.raises(TimeoutException):
        manager._js_click(element)
    manager.driver.execute_script.assert_not_called()


def test_preparation_retry_is_safe(manager, flat, mocker):
    prepare = mocker.patch.object(manager, "_prepare_form", side_effect=[TimeoutException("missing contact button"), Mock()])
    manager.driver.execute_script.return_value = []
    mocker.patch.object(manager, "_submission_confirmed", return_value=True)
    mocker.patch.object(manager, "_log_application")
    assert manager.apply(flat)
    assert prepare.call_count == 2


def test_invalid_form_is_not_submitted(manager, flat, mocker):
    button = Mock()
    mocker.patch.object(manager, "_prepare_form", return_value=button)
    manager.driver.execute_script.return_value = ["powermail_field_new_required_field"]
    with pytest.raises(ApplicationError, match="validation"):
        manager.apply(flat)
    button.click.assert_not_called()
    assert not manager.state.data["pending"]


def test_existing_application_is_never_resubmitted(manager, flat, mocker):
    manager.log_path.write_text(f"old log entry\nhash: {flat.hash}\n")
    prepare = mocker.patch.object(manager, "_prepare_form")
    assert not manager.apply(flat)
    prepare.assert_not_called()


def test_transient_queries_alert_only_after_three_runs(tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(main, "setup_loggers")
    mocker.patch.object(main, "main", side_effect=TimeoutException("Listing results did not load"))
    telegram = mocker.patch.object(main, "send_telegram", return_value=True)
    main.run()
    main.run()
    telegram.assert_not_called()
    main.run()
    main.run()
    telegram.assert_called_once()
    assert "3 consecutive" in telegram.call_args.args[0]
    assert RunState().data["query_failures"] == 4
    main.main.side_effect = None
    main.main.side_effect = lambda state: state.query_succeeded()
    main.run()
    assert RunState().data["query_failures"] == 0
    assert "query" not in RunState().data["incidents"]


def test_telegram_failure_triggers_email_fallback(state, tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(main, "send_telegram", return_value=False)
    with pytest.raises(main.NotificationDeliveryError):
        main.notify_issue(state, "run", "Invalid configuration")
    assert not state.data["incidents"]


def test_delivered_incident_deduplicates_without_public_message_content(state, tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "Sofoca/example")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    telegram = mocker.patch.object(main, "send_telegram", return_value=True)
    message = "Application failed for Example flat; check form"
    main.notify_issue(state, "flat", message)
    main.notify_issue(RunState(state.path), "flat", message)
    telegram.assert_called_once()
    assert "https://github.com/Sofoca/example/actions/runs/123" in telegram.call_args.args[0]
    assert "Example flat" not in state.path.read_text()


def test_bad_config_does_not_expose_secret_lines(monkeypatch):
    secret = "private.person@example.invalid"
    monkeypatch.setenv("USER_CONFIG", f"email: {secret}\nname: [unterminated")
    with pytest.raises(ValueError) as error:
        ConfigLoader().load_user_data()
    assert secret not in str(error.value)
    assert error.value.__suppress_context__


def test_malformed_state_is_not_silently_discarded(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("not json")
    with pytest.raises(json.JSONDecodeError):
        RunState(path)


def test_application_failure_alerts_immediately_and_other_flats_continue(state, tmp_path, monkeypatch, mocker):
    monkeypatch.chdir(tmp_path)
    mocker.patch.object(main, "User", return_value=SimpleNamespace(first_name="Test"))
    mocker.patch.object(main, "ConfigLoader")
    mocker.patch.object(main.webdriver, "Chrome")
    flats = [Mock(hash=f"hash-{i}", title=f"Flat {i}", detail_link=f"https://www.wbm.de/flat-{i}",
                  base_rent=800, zip_code="10115", size=60, rooms=2, total_rent=1000, wbs=False,
                  property_attrs=[]) for i in range(2)]
    scraper = mocker.patch.object(main, "FlatScraper").return_value
    scraper.get_flats.return_value = flats
    manager = mocker.patch.object(main, "ApplicationManager").return_value
    manager.has_applied.return_value = False
    manager.apply.side_effect = [TimeoutException("Application failed while opening the contact form"), True]
    telegram = mocker.patch.object(main, "send_telegram", return_value=True)
    main.main(state)
    assert manager.apply.call_count == 2
    messages = [call.args[0] for call in telegram.call_args_list]
    assert "opening the contact form" in messages[0]
    assert "https://www.wbm.de/flat-0" in messages[0]
    assert "applied to a flat" in messages[1]
