"""docker/entrypoint.sh: the startup order, and that any failure stops it.

Fake "python" and "flask" commands on PATH record each call instead of
running, so the shell logic is tested on its own, without a database.
"""

import os
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[1] / "docker" / "entrypoint.sh"

FAKE_COMMAND = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
if [ "$1" = "-" ]; then cat > /dev/null; fi
if [ -n "$FAKE_FAIL_ON" ]; then
    case "$(basename "$0") $*" in *"$FAKE_FAIL_ON"*) echo "fake failure" >&2; exit 3 ;; esac
fi
exit 0
"""


@pytest.fixture
def run_entrypoint(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("python", "flask"):
        fake = bin_dir / name
        fake.write_text(FAKE_COMMAND)
        fake.chmod(0o755)
    log = tmp_path / "calls.log"

    def run(fail_on=""):
        env = {
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_LOG": str(log),
            "FAKE_FAIL_ON": fail_on,
        }
        result = subprocess.run(
            ["sh", str(ENTRYPOINT), "echo", "APP STARTED"],
            env=env,
            capture_output=True,
            text=True,
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return result, calls

    return run


def test_waits_for_postgres_then_migrates_then_starts(run_entrypoint):
    result, calls = run_entrypoint()

    assert result.returncode == 0, result.stderr
    assert calls == ["python -", "flask db upgrade"]
    assert "APP STARTED" in result.stdout


def test_failed_migration_stops_startup(run_entrypoint):
    result, calls = run_entrypoint(fail_on="flask db upgrade")

    assert result.returncode != 0
    assert "ERROR: Database migrations failed" in result.stderr
    assert "the app was not started" in result.stderr
    assert "APP STARTED" not in result.stdout


def test_unreachable_database_stops_before_migrating(run_entrypoint):
    result, calls = run_entrypoint(fail_on="python -")

    assert result.returncode != 0
    assert "ERROR: Could not reach Postgres" in result.stderr
    assert "flask db upgrade" not in calls
    assert "APP STARTED" not in result.stdout


def test_entrypoint_is_executable():
    assert os.access(ENTRYPOINT, os.X_OK)
