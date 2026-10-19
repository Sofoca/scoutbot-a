"""Exercise the final Actions notification step without sending messages."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml


def workflow_failure_notifier():
    workflow = yaml.safe_load(Path('.github/workflows/run-scraper.yml').read_text())
    script = workflow['jobs']['run-scraper']['steps'][-1]['run']
    return script.split("python3 - <<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]


@pytest.mark.parametrize('failures,telegram_ok,expected', [
    ([], False, 0),
    (['dependencies'], True, 0),
    (['persist'], True, 0),
    (['scraper'], False, 'Telegram delivery unavailable'),
    (['chrome'], False, 'Telegram delivery unavailable'),
])
def test_email_only_when_an_actionable_failure_cannot_reach_telegram(monkeypatch, mocker, failures, telegram_ok, expected):
    monkeypatch.setenv('STEP_RESULTS', json.dumps({name: {'outcome': 'failure'} for name in failures}))
    monkeypatch.setenv('GITHUB_REPOSITORY', 'Sofoca/test')
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'test-token')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'test-channel')
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({'ok': telegram_ok}).encode()
    request = mocker.patch('urllib.request.urlopen', return_value=response)
    mocker.patch('time.sleep')
    with pytest.raises(SystemExit) as result:
        exec(compile(workflow_failure_notifier(), '<workflow notification>', 'exec'), {})
    if isinstance(expected, int):
        assert result.value.code == expected
    else:
        assert expected in result.value.code
    if not failures:
        request.assert_not_called()
    elif 'persist' in failures:
        assert b'Application history could not be saved' in request.call_args.args[0].data
