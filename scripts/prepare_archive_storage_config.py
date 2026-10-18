#!/usr/bin/env python3
"""Render private static SeaweedFS identities from mounted archive credentials.

Run locally with synthetic credentials or as an owner during private deployment.
No bootstrap administrator or anonymous identity is included in the result.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/archive"))
from academy_archive import ArchiveConfig, ArchiveError, atomic_private, canonical, private_bytes


def render(config, writer, reader):
    identities, policies = [], []
    for role, credentials in (("writer", writer), ("reader", reader)):
        if set(credentials) != {"access_key_id", "secret_access_key"} or not all(isinstance(value, str) and value for value in credentials.values()):
            raise ArchiveError("Archive credential file has an invalid format")
        name = "AcademyArchive" + role.title()
        document = json.loads((ROOT / f"deploy/archive/{role}-policy.example.json").read_text())
        material = json.dumps(document).replace("ncacademy-audit", config.bucket).replace("REPLACE-TENANT-SHA256", config.prefix.split("/")[1])
        policies.append({"name": name, "content": json.dumps(json.loads(material), separators=(",", ":"))})
        identities.append({"name": "academy-archive-" + role, "credentials": [{"accessKey": credentials["access_key_id"], "secretKey": credentials["secret_access_key"]}], "policyNames": [name]})
    if writer["access_key_id"] == reader["access_key_id"] or writer["secret_access_key"] == reader["secret_access_key"]:
        raise ArchiveError("Writer and owner reader must use distinct archive-only credentials")
    return {"identities": identities, "policies": policies}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--writer", type=Path, required=True)
    parser.add_argument("--reader", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ArchiveError("Refusing to replace an existing private identity file")
        content = render(ArchiveConfig.load(args.config), json.loads(private_bytes(args.writer)), json.loads(private_bytes(args.reader)))
        atomic_private(args.output, canonical(content))
    except Exception:
        print("Private archive identity rendering failed; no credential values are displayed", file=sys.stderr)
        return 1
    print("Private archive identities rendered; no administrator or anonymous identity included")
    return 0


if __name__ == "__main__":
    sys.exit(main())
