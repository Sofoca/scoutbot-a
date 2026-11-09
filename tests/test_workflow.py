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


def test_uv_download_outage_falls_back_instead_of_failing_a_stage():
    """A GitHub Releases 504 in setup-uv must not surface as a failed 'uv' stage."""
    workflow = yaml.safe_load(Path('.github/workflows/run-scraper.yml').read_text())
    steps = workflow['jobs']['run-scraper']['steps']
    setup_uv = next(s for s in steps if str(s.get('uses', '')).startswith('astral-sh/setup-uv'))
    assert 'id' not in setup_uv, 'setup-uv outcome must stay out of the steps context'
    assert setup_uv['continue-on-error'] is True
    ids = [s.get('id') for s in steps]
    assert ids.index('uv') == steps.index(setup_uv) + 1
    fallback = steps[ids.index('uv')]['run']
    assert 'command -v uv' in fallback and 'pip' in fallback
    for stage in ('python', 'dependencies'):
        assert 'for i in' in steps[ids.index(stage)]['run'], f'{stage} must retry transient download errors'
