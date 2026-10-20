"""Check the age and completeness of a private archive success receipt.

This probe does not contact storage, read student data or establish retention.
It fails when an exporter has stopped reporting verified successful runs.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time


def check_health(path, *, max_age_seconds=180, clock=None):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or path.stat().st_size > 8192 or not 60 <= max_age_seconds <= 3600:
        raise ValueError("Archive health receipt is unavailable")
    receipt = json.loads(path.read_bytes())
    if not isinstance(receipt, dict) or receipt.get("status") != "archive_current" or type(receipt.get("lag_events")) is not int or receipt["lag_events"] != 0 or type(receipt.get("archived_sequence")) is not int or receipt["archived_sequence"] < 0 or receipt["archived_sequence"] != receipt.get("database_sequence"):
        raise ValueError("Archive verification receipt is incomplete")
    checked = datetime.fromisoformat(receipt["checked_at"])
    if checked.tzinfo is None:
        raise ValueError("Archive health timestamp lacks timezone")
    epoch = time.time() if clock is None else clock
    age = epoch - checked.timestamp()
    if age < -30 or age > max_age_seconds:
        raise ValueError("Archive verification receipt is stale or in the future")
    return {"status": "archive_recently_verified", "age_seconds": max(0, int(age)), "archived_sequence": receipt["archived_sequence"], "lag_events": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health-file", type=Path, required=True)
    parser.add_argument("--max-age-seconds", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(check_health(args.health_file, max_age_seconds=args.max_age_seconds), sort_keys=True))
        return 0
    except Exception:
        print(json.dumps({"status": "archive_health_failed", "action": "Investigate private archive job, freshness and independently retained checkpoint"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
