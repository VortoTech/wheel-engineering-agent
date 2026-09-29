"""Portable skill integration: copied package, real runtime, preserved caller files."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def invoke(tmp_path, *args, runtime=True):
    installed = tmp_path / 'installed' / 'wheel-engineering'
    if not installed.exists():
        shutil.copytree(ROOT / 'skills/wheel-engineering', installed)
    env = {k: v for k, v in os.environ.items() if k not in ('WHEEL_ENGINEERING_RUNTIME', 'WHEEL_ENGINEERING_PYTHON')}
    command = [sys.executable, str(installed / 'scripts/run.py')]
    if runtime:
        command += ['--runtime', str(ROOT), '--python', sys.executable]
    return subprocess.run([*command, *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)


def test_copied_skill_reports_real_runtime_imports(tmp_path):
    result = invoke(tmp_path, 'doctor')
    assert result.returncode == 0, result.stderr
    assert '"runtime_imports": "ok"' in result.stdout


def test_copied_skill_dispatches_photo_cli_from_other_directory(tmp_path):
    result = invoke(tmp_path, 'photo', '--help')
    assert result.returncode == 0, result.stderr
    assert '--spec-evidence' in result.stdout and '--kernel' in result.stdout


def test_existing_output_is_preserved_before_runtime_execution(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_text('previous evidence')
    result = invoke(tmp_path, 'text', 'a wheel', '--out', 'existing')
    assert result.returncode == 2
    assert 'existing results are preserved' in result.stderr
    assert marker.read_text() == 'previous evidence'


def test_missing_runtime_is_actionable_without_environment_dump(tmp_path):
    result = invoke(tmp_path, 'doctor', runtime=False)
    assert result.returncode == 2
    assert 'Set WHEEL_ENGINEERING_RUNTIME' in result.stderr
