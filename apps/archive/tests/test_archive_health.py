from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from archive_health import check_health, main


def test_recent_complete_receipt_passes_without_exposing_checkpoint(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"status": "archive_current", "checked_at": datetime.fromtimestamp(1000, timezone.utc).isoformat(), "database_sequence": 17, "archived_sequence": 17, "lag_events": 0, "checkpoint": {"private": "never print this"}}))
    result = check_health(path, clock=1100)
    assert result == {"status": "archive_recently_verified", "age_seconds": 100, "archived_sequence": 17, "lag_events": 0}
    assert "private" not in json.dumps(result)
    with pytest.raises(ValueError, match="stale"):
        check_health(path, clock=1181)
    with pytest.raises(ValueError, match="future"):
        check_health(path, clock=900)


@pytest.mark.parametrize("change", [{"status": "archive_failed"}, {"archived_sequence": 16}, {"lag_events": 1}, {"lag_events": False}, {"checked_at": "2026-10-20T00:00:00"}])
def test_bad_or_incomplete_receipt_refuses_health(tmp_path, change):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"status": "archive_current", "checked_at": datetime.fromtimestamp(1000, timezone.utc).isoformat(), "database_sequence": 17, "archived_sequence": 17, "lag_events": 0, **change}))
    with pytest.raises(ValueError):
        check_health(path, clock=1100)


def test_missing_health_cli_is_nonzero_and_sanitized(tmp_path, capsys):
    assert main(["--health-file", str(tmp_path / "missing")]) == 1
    captured = capsys.readouterr()
    assert "archive_health_failed" in captured.err
    assert str(tmp_path) not in captured.err
