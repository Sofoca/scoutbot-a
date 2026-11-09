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


def run_scraper_steps():
    return yaml.safe_load(Path('.github/workflows/run-scraper.yml').read_text())['jobs']['run-scraper']['steps']


def test_downloads_with_fallbacks_stay_out_of_the_steps_context():
    """Only verified stages carry an id; failed downloads must not trigger stage alerts."""
    steps = run_scraper_steps()
    for step in steps:
        uses = str(step.get('uses', ''))
        if uses.startswith(('actions/checkout', 'astral-sh/setup-uv', 'browser-actions/setup-chrome')):
            assert 'id' not in step and step['continue-on-error'] is True, uses
    ids = [s.get('id') for s in steps]
    assert ids.index('checkout') < ids.index('history')
    assert 'hashFiles' in steps[ids.index('checkout') - 1]['if'], 'checkout must be retried when files are missing'


def _fake_binary(directory, name, version):
    path = directory / name
    path.write_text(f'#!/bin/sh\necho "{name} {version}"\n')
    path.chmod(0o755)
    return path


def _step_script(name):
    return next(s for s in run_scraper_steps() if s.get('name') == name)['run']


def _run_chrome_steps(tmp_path, preinstalled, downloaded):
    """Run the probe and select steps the way Actions would, with fake binaries."""
    import subprocess
    system, driver_dir, download = tmp_path / 'system', tmp_path / 'chromedriver-linux64', tmp_path / 'download'
    for d in (system, driver_dir, download):
        d.mkdir()
    if preinstalled:
        _fake_binary(system, 'google-chrome', preinstalled[0])
        _fake_binary(driver_dir, 'chromedriver', preinstalled[1])
    env_file, output = tmp_path / 'env', tmp_path / 'output'
    env_file.touch(); output.touch()
    base = {'PATH': f'{system}:{download}', 'CHROMEWEBDRIVER': str(driver_dir),
            'GITHUB_ENV': str(env_file), 'GITHUB_OUTPUT': str(output)}
    probe = subprocess.run(['/bin/bash', '-e', '-c', _step_script('Check preinstalled Chrome')],
                           capture_output=True, text=True, env=base)
    assert probe.returncode == 0, probe.stdout + probe.stderr
    exported = dict(line.split('=', 1) for line in env_file.read_text().splitlines())
    download_ran = not exported.get('PREINSTALLED_CHROME')
    if download_ran and downloaded:
        _fake_binary(download, 'chrome', downloaded[0])
        _fake_binary(download, 'chromedriver', downloaded[1])
    select = subprocess.run(['/bin/bash', '-e', '-c', _step_script('Select a matching Chrome and chromedriver')],
                            capture_output=True, text=True, env={**base, **exported})
    chosen = dict(line.split('=', 1) for line in output.read_text().splitlines())
    return download_ran, select.returncode, chosen, (system, driver_dir, download)


def test_matching_preinstalled_chrome_skips_the_download(tmp_path):
    download_ran, code, chosen, (system, driver_dir, _) = _run_chrome_steps(
        tmp_path, preinstalled=('140.0.5', '140.0.5'), downloaded=('142.0.1', '142.0.1'))
    assert not download_ran and code == 0
    assert chosen == {'chrome-path': str(system / 'google-chrome'), 'chromedriver-path': str(driver_dir / 'chromedriver')}


@pytest.mark.parametrize('preinstalled', [('140.0.5', '139.0.1'), None])
def test_unusable_preinstalled_chrome_falls_back_to_the_download(tmp_path, preinstalled):
    download_ran, code, chosen, (_, _, download) = _run_chrome_steps(
        tmp_path, preinstalled=preinstalled, downloaded=('142.0.1', '142.0.1'))
    assert download_ran and code == 0
    assert chosen == {'chrome-path': str(download / 'chrome'), 'chromedriver-path': str(download / 'chromedriver')}


@pytest.mark.parametrize('downloaded', [None, ('142.0.1', '141.0.9')])
def test_chrome_selection_fails_without_a_matching_pair(tmp_path, downloaded):
    download_ran, code, chosen, _ = _run_chrome_steps(tmp_path, preinstalled=None, downloaded=downloaded)
    assert download_ran and code != 0 and chosen == {}
