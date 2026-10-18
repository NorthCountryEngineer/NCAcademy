#!/usr/bin/env python3
"""Verify a complete tenant audit export against an optional trusted checkpoint.

This tool is offline and never reads application configuration or credentials.
A checkpoint must be obtained from an independent trusted retention boundary.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify(export, checkpoint=None):
    tenant = export["tenant_id"]
    if not isinstance(tenant, str) or not tenant:
        raise ValueError("Export tenant is missing")
    events = export["events"]
    if not isinstance(events, list):
        raise ValueError("Export events must be a list")
    digest = "0" * 64
    sequence = 0
    required = {"id", "tenant_id", "sequence", "session_id", "actor_id", "event_type", "payload", "previous_hash", "created_at", "hash"}
    for event in events:
        if set(event) != required:
            raise ValueError("Unexpected event schema")
        sequence += 1
        if event["tenant_id"] != tenant or type(event["sequence"]) is not int or event["sequence"] != sequence or event["previous_hash"] != digest:
            raise ValueError("Audit order, tenant or predecessor mismatch")
        material = {k: v for k, v in event.items() if k != "hash"}
        digest = hashlib.sha256(canonical(material).encode()).hexdigest()
        if event["hash"] != digest:
            raise ValueError("Audit event digest mismatch")
    head = export["head"]
    if head != {"sequence": sequence, "digest": digest}:
        raise ValueError("Export head differs from event chain")
    if checkpoint is not None:
        if checkpoint.get("tenant_id") != tenant or checkpoint.get("sequence") != sequence or checkpoint.get("digest") != digest:
            raise ValueError("Independent checkpoint differs: possible truncation or rewrite")
    return {"tenant_id": tenant, "sequence": sequence, "digest": digest, "independently_anchored": checkpoint is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    try:
        export = json.loads(args.export.read_text())
        checkpoint = json.loads(args.checkpoint.read_text()) if args.checkpoint else None
        result = verify(export, checkpoint)
    except (OSError, ValueError, TypeError, KeyError):
        print("Audit verification FAILED: malformed, modified or mismatched export/checkpoint", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    if not checkpoint:
        print("Chain is internally consistent; no independent checkpoint was supplied.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
