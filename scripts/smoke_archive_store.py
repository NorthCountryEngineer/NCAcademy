#!/usr/bin/env python3
"""Explicit disposable SeaweedFS TLS/S3 contract smoke; synthetic data only.

Requires Docker and apps/archive/requirements.lock.txt. Nothing is deployed to
AWS or an existing cluster. Only this script's uniquely named container/network
and temporary files are created/removed. No ambient cloud credentials are used.
"""
import argparse
from datetime import datetime, timedelta, timezone
import http.client
import ipaddress
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/archive"))
from academy_archive import Archive, ArchiveConfig, DatabaseSource, S3Objects, ZERO, canonical, export_once, sha

IMAGE = "chrislusf/seaweedfs@sha256:4e61d15fd35994cb1e43e1e553dff106794841fd9a99ade2fc8c8bfce4d7872d"


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


def fixture(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(datetime.now(timezone.utc) - timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
    (folder / "tls.crt").write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (folder / "tls.key").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    credentials = {name: {"access_key_id": "synthetic-" + name, "secret_access_key": uuid4().hex + uuid4().hex} for name in ("operator", "writer", "reader")}
    writer_policy = json.loads((ROOT / "deploy/archive/writer-policy.example.json").read_text().replace("REPLACE-TENANT-SHA256", sha(b"synthetic-tenant")))
    reader_policy = json.loads((ROOT / "deploy/archive/reader-policy.example.json").read_text().replace("REPLACE-TENANT-SHA256", sha(b"synthetic-tenant")))
    config = {"identities": [{"name": name, "credentials": [{"accessKey": value["access_key_id"], "secretKey": value["secret_access_key"]}], **({"actions": ["Admin", "Read", "Write", "List", "Tagging"]} if name == "operator" else {"policyNames": ["AcademyArchive" + name.title()]})} for name, value in credentials.items()], "policies": [{"name": "AcademyArchiveWriter", "content": json.dumps(writer_policy)}, {"name": "AcademyArchiveReader", "content": json.dumps(reader_policy)}]}
    (folder / "s3.json").write_bytes(canonical(config))
    for path in folder.iterdir():
        path.chmod(0o444)  # Synthetic files inside a private 0700 temp parent.
    return credentials


def synthetic_source(folder):
    from sqlalchemy import create_engine, text
    events, previous = [], ZERO
    kinds = ("tutor.admitted", "tutor.completed", "lesson.admitted", "lesson.completed", "curated_lesson.admitted", "curated_lesson.completed", "student.agent_feedback")
    for sequence, kind in enumerate(kinds, 1):
        event = {"id": str(sequence), "tenant_id": "synthetic-tenant", "sequence": sequence, "session_id": "synthetic-session", "actor_id": "synthetic-student", "event_type": kind, "payload": {"message": "Synthetic student question", "attempt": "Synthetic student attempt", "approved_text": "Exact reviewed teaching text", "learning_preferences_snapshot": {"version": 1, "weights": {"warmth": 0.7}}, "feedback": {"accuracy": "unsure"}}, "previous_hash": previous, "created_at": "2026-10-18T12:00:00+00:00"}
        event["hash"] = sha(canonical(event))
        previous = event["hash"]
        events.append(event)
    url = "sqlite:///" + str(folder / "synthetic.db")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE audit_heads (tenant_id TEXT, sequence INTEGER, digest TEXT)"))
        connection.execute(text("CREATE TABLE audit_events (id TEXT, tenant_id TEXT, sequence INTEGER, session_id TEXT, actor_id TEXT, event_type TEXT, payload TEXT, previous_hash TEXT, created_at TEXT, hash TEXT)"))
        connection.execute(text("INSERT INTO audit_heads VALUES ('synthetic-tenant',:sequence,:digest)"), {"sequence": len(events), "digest": previous})
        for event in events:
            connection.execute(text("INSERT INTO audit_events VALUES (:id,:tenant_id,:sequence,:session_id,:actor_id,:event_type,:payload,:previous_hash,:created_at,:hash)"), {**event, "payload": canonical(event["payload"]).decode()})
    engine.dispose()
    return events, DatabaseSource(url, "synthetic-tenant", local_test=True)


def run(image=IMAGE, native_binary=None):
    import boto3
    from botocore.config import Config
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from botocore.exceptions import ClientError
    name = "ncacademy-archive-contract-" + uuid4().hex[:10]
    checks = []
    native_process = None
    with tempfile.TemporaryDirectory(prefix=name + "-", dir="/tmp") as temporary:
        folder = Path(temporary)
        mounted = folder / "mounted"
        mounted.mkdir(mode=0o755)
        credentials = fixture(mounted)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        endpoint = f"https://127.0.0.1:{port}"
        ca = str(mounted / "tls.crt")
        volume = name + "-data"
        try:
            if native_binary:
                import random
                if os.geteuid() == 0 or not Path(native_binary).is_absolute():
                    raise RuntimeError("Native synthetic storage requires an unprivileged caller and absolute reviewed binary path")
                ports = []
                for _ in range(4):
                    for attempt in range(100):
                        candidate = random.SystemRandom().randrange(20000, 29000)
                        try:
                            with socket.socket() as probe:
                                probe.bind(("127.0.0.1", candidate))
                            if candidate not in ports:
                                ports.append(candidate)
                                break
                        except OSError:
                            continue
                    else:
                        raise RuntimeError("No private synthetic service port available")
                data = folder / "data"
                data.mkdir()
                (folder / "security.toml").write_text("# Isolated synthetic configuration; never load user-global security settings.\n")
                (folder / "filer.toml").write_text('[leveldb2]\nenabled = true\ndir = "' + str(data / "filerldb2") + '"\n')
                environment = {key: value for key, value in os.environ.items() if not key.startswith(("AWS_", "WEED_", "S3_"))}
                with (folder / "storage.log").open("wb") as log:
                    native_process = subprocess.Popen([str(native_binary), "-logtostderr=true", "server", "-ip=127.0.0.1", "-ip.bind=127.0.0.1", "-dir=" + str(data), "-master.port=" + str(ports[0]), "-volume.port=" + str(ports[1]), "-filer.port=" + str(ports[2]), "-master.volumeSizeLimitMB=32", "-volume.max=2", "-master.telemetry=false", "-filer", "-filer.encryptVolumeData=true", "-s3", "-s3.port=" + str(ports[3]), "-s3.encryptVolumeData=true", "-s3.port.https=" + str(port), "-s3.cert.file=" + str(mounted / "tls.crt"), "-s3.key.file=" + str(mounted / "tls.key"), "-s3.config=" + str(mounted / "s3.json"), "-s3.iam.readOnly=true"], cwd=folder, env=environment, stdout=log, stderr=subprocess.STDOUT)
            else:
                docker("volume", "create", volume)
                docker("run", "--rm", "--name", name + "-init", "--network", "none", "--user", "0:0", "--mount", f"type=volume,source={volume},target=/data", "--entrypoint", "sh", image, "-c", "chown 10001:10001 /data")
                docker("network", "create", name)
                docker("run", "--detach", "--name", name, "--network", name, "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=134217728", "--user", "10001:10001", "--publish", f"127.0.0.1:{port}:8334", "--mount", f"type=bind,source={mounted},target=/test-config,readonly", "--mount", f"type=volume,source={volume},target=/data", image,
                    "server", "-ip=127.0.0.1", "-ip.bind=0.0.0.0", "-dir=/data", "-master.volumeSizeLimitMB=32", "-volume.max=2", "-master.telemetry=false", "-filer", "-filer.encryptVolumeData=true", "-s3", "-s3.encryptVolumeData=true", "-s3.port.https=8334", "-s3.cert.file=/test-config/tls.crt", "-s3.key.file=/test-config/tls.key", "-s3.config=/test-config/s3.json", "-s3.iam.readOnly=true")
            owner = boto3.Session(aws_access_key_id=credentials["operator"]["access_key_id"], aws_secret_access_key=credentials["operator"]["secret_access_key"], region_name="us-east-1").client("s3", endpoint_url=endpoint, verify=ca, config=Config(s3={"addressing_style": "path"}, proxies={}, retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=10, request_checksum_calculation="when_required"))
            context = ssl.create_default_context(cafile=ca)
            last_reason = "listener_unavailable"
            for _ in range(600):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
                        with context.wrap_socket(connection, server_hostname="127.0.0.1"):
                            pass
                    break
                except Exception as error:
                    last_reason = type(error).__name__
                    if native_process and native_process.poll() is not None:
                        raise RuntimeError("Synthetic native storage exited during startup: " + (folder / "storage.log").read_text()[-3000:])
                    time.sleep(1)
            else:
                raise RuntimeError("Disposable private TLS storage did not become ready: " + last_reason)
            owner.list_buckets()  # Authentication failures are distinct from listener startup.
            owner.create_bucket(Bucket="ncacademy-audit", ObjectLockEnabledForBucket=True)
            owner.put_bucket_versioning(Bucket="ncacademy-audit", VersioningConfiguration={"Status": "Enabled"})
            owner.put_object_lock_configuration(Bucket="ncacademy-audit", ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Days": 1}}})
            checks.append("verified_tls_private_nonroot_storage")
            cfg = ArchiveConfig("synthetic-tenant", endpoint, (endpoint,), "ncacademy-audit", "ncacademy/" + sha(b"synthetic-tenant"), ca, retention_days=1, batch_size=2)
            writer_file = folder / "writer.json"
            writer_file.write_bytes(canonical(credentials["writer"]))
            objects = S3Objects(cfg, writer_file)
            key = cfg.prefix + "/contract/conditional"
            first = objects.create(key, b"first synthetic encrypted blob")
            assert objects.create(key, b"must never replace first") == first
            assert objects.get(key) == first
            checks.append("conditional_create_no_overwrite_and_compliance_retention")
            # Deliberately separate SDK clients bypass the application's own
            # method guard so this proves the server's IAM/retention boundary.
            raw_writer = boto3.Session(aws_access_key_id=credentials["writer"]["access_key_id"], aws_secret_access_key=credentials["writer"]["secret_access_key"], region_name="us-east-1").client("s3", endpoint_url=endpoint, verify=ca, config=Config(s3={"addressing_style": "path"}, proxies={}, retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=10, request_checksum_calculation="when_required"))
            version = owner.head_object(Bucket=cfg.bucket, Key=key)["VersionId"]
            try:
                raw_writer.delete_object(Bucket=cfg.bucket, Key=key, VersionId=version)
            except ClientError as error:
                assert error.response["ResponseMetadata"]["HTTPStatusCode"] in (400, 403)
            else:
                raise AssertionError("Writer deletion was not refused by storage IAM")
            checks.append("server_writer_delete_refused")
            try:
                owner.delete_object(Bucket=cfg.bucket, Key=key, VersionId=version)
            except ClientError as error:
                assert error.response["ResponseMetadata"]["HTTPStatusCode"] in (400, 403)
            else:
                raise AssertionError("COMPLIANCE retained-version deletion was not refused")
            checks.append("server_compliance_retained_version_delete_refused")
            anonymous = http.client.HTTPSConnection("127.0.0.1", port, context=context, timeout=10)
            anonymous.request("GET", "/" + cfg.bucket + "/" + key)
            response = anonymous.getresponse()
            assert response.status == 403
            response.read()
            anonymous.close()
            checks.append("anonymous_evidence_read_refused")
            reader_file = folder / "reader.json"
            reader_file.write_bytes(canonical(credentials["reader"]))
            read_only = S3Objects(cfg, reader_file, writable=False)
            assert read_only.get(key) == first
            raw_reader = boto3.Session(aws_access_key_id=credentials["reader"]["access_key_id"], aws_secret_access_key=credentials["reader"]["secret_access_key"], region_name="us-east-1").client("s3", endpoint_url=endpoint, verify=ca, config=Config(s3={"addressing_style": "path"}, proxies={}, retries={"total_max_attempts": 1}, connect_timeout=2, read_timeout=10, request_checksum_calculation="when_required"))
            foreign = "ncacademy/" + sha(b"another-tenant") + "/contract/private"
            owner.put_object(Bucket=cfg.bucket, Key=foreign, Body=b"other synthetic tenant")
            for client, operation in ((raw_reader, "foreign_read"), (raw_writer, "foreign_read"), (raw_reader, "write")):
                try:
                    if operation == "foreign_read":
                        response = client.get_object(Bucket=cfg.bucket, Key=foreign)
                        response["Body"].close()
                    else:
                        client.put_object(Bucket=cfg.bucket, Key=cfg.prefix + "/contract/reader-must-not-write", Body=b"denied", IfNoneMatch="*")
                except ClientError as error:
                    assert error.response["ResponseMetadata"]["HTTPStatusCode"] == 403
                else:
                    raise AssertionError("Server archive reader/writer scope was not enforced")
            checks.append("server_owner_reader_is_read_only_and_tenant_scoped")
            signing = Ed25519PrivateKey.generate()
            archive = Archive(cfg, objects, os.urandom(32), signing.public_key().public_bytes_raw(), signing.private_bytes_raw())
            original, source = synthetic_source(folder)
            cursor_path = folder / "cursor.json"
            result = export_once(archive, source, cursor_path)
            trusted = result["checkpoint"]
            restored, checkpoint = archive.export(trusted)
            assert restored["events"] == original and checkpoint == trusted
            assert b"Synthetic student question" not in objects.get(json.loads(objects.get(archive.commit_key(1)))["body"]["object_key"])
            checks.append("encrypted_signed_complete_session_and_feedback_roundtrip")
            assert export_once(archive, source, cursor_path)["checkpoint"] == trusted
            cursor_path.unlink()
            assert export_once(archive, source, cursor_path)["checkpoint"] == trusted
            checks.append("idempotent_reexport_and_lost_cursor_recovery")
            return {"status": "passed", "runtime": "private_native_storage" if native_binary else "disposable_container", "image": None if native_binary else image, "binary_sha256": sha(Path(native_binary).read_bytes()) if native_binary else None, "checks": checks, "events": len(original), "public_exposure": False, "cloud_changes": False}
        finally:
            if native_process:
                native_process.terminate()
                try:
                    native_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    native_process.kill()
                    native_process.wait()
            elif not native_binary:
                subprocess.run(["docker", "rm", "--force", name, name + "-init"], capture_output=True)
                subprocess.run(["docker", "network", "rm", name], capture_output=True)
                subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Explicitly run this disposable local storage contract")
    parser.add_argument("--image", default=IMAGE)
    parser.add_argument("--native-binary", type=Path, help="Absolute hash-verified upstream SeaweedFS binary for a private unprivileged native contract")
    args = parser.parse_args()
    if not args.run:
        parser.error("Explicit --run is required")
    print(json.dumps(run(args.image, native_binary=args.native_binary), indent=2))


if __name__ == "__main__":
    main()
