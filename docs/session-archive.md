# Persistent session archive

The separate `apps/archive` worker retains the complete tenant audit chain in a private S3-compatible object store. The proposed self-hosted store is **SeaweedFS OSS 4.48**, under Apache-2.0. It has an S3 interface, Kubernetes deployment options, persistent storage, TLS, conditional creation and Object Lock. These features are documented by its [project](https://github.com/seaweedfs/seaweedfs), [conditional-operation contract](https://github.com/seaweedfs/seaweedfs/wiki/S3-Conditional-Operations) and [retention contract](https://github.com/seaweedfs/seaweedfs/wiki/S3-Object-Lock-and-Retention). Garage is a smaller alternative, but its current [compatibility matrix](https://garagehq.deuxfleurs.fr/documentation/reference-manual/s3-compatibility/) lacks Object Lock; it does not meet this package's admission contract.

Open-source software removes the object-store license/subscription charge. Persistent disks, replicas, compute, network, backups and operations still have costs. No existing Kubernetes cluster has been established by this work: the current NCE Core recipe runs the Academy API on **Fargate**. Storage and the archive worker can run in a separately operated private Kubernetes cluster without moving the API or granting it an AWS task role. The package does not deploy a cluster, change cloud resources or enable live student/model admission.

## Evidence and boundaries

The archive reads only `audit_events` and `audit_heads` through a distinct SELECT-only PostgreSQL identity. Each run uses verified TLS and a read-only repeatable-read snapshot, verifies the full original tenant hash chain against its database head, then appends new contiguous events. A missing/truncated database head or a database rollback behind a surviving remote commit fails closed, including a lost local cursor and an empty restored database. A concurrent exporter that advances beyond the current database snapshot can cause a conservative failure; the next scheduled run retries with a fresh snapshot. Tenant ID, original sequence, timestamp, actor/session IDs, payload, predecessor and event hash are retained without renumbering, rehashing or redaction.

Existing admitted/send/terminal events preserve student questions and attempts, visible output, exact source/corpus/teacher/model/policy snapshots, refusals, errors, usage and uncertain sends. Curation events include released multimedia descriptors and cited teaching text; learning events include actions/reflections. Homework events retain original questions, course/teacher routing, bounded conversation context, immutable outcomes and exact lesson links. Voice events retain consent, recording hashes/duration, released transcripts, exact spoken text and playback evidence; microphone bytes and generated audio/ciphertext are excluded. Student intake and feedback retain the exact preference revision, algorithm/effective weights and before/resulting feedback snapshots. This lets an owner reconstruct what a student actually saw and how the bounded teaching behavior changed. The audit records observable service activity; it does not contain hidden reasoning or establish what the child did outside the application.

The worker never reads the `records`, login, OAuth or integration/vault tables. Encrypted assignment-photo bytes and keys remain outside this JSON archive; the existing controlled database/photo backup is still needed for original binary evidence. Known credential/binary field names cause export to fail, leaving the original audit chain intact, instead of silently stripping values and invalidating its hashes. User-entered prose remains student evidence and must be handled as sensitive data. A future feature must first write its complete safe evidence to the audit chain; recording only a mutable record ID cannot make that feature reconstructible.

## Durable append protocol

1. Canonical batches contain at most 100 events, with a 32 MiB object bound and smaller batches for large payloads. They are AES-256-GCM encrypted before leaving the worker. The authentication context binds the configured archive and object key; encryption keys are mounted privately, never sent to S3.
2. Encrypted segments use a plaintext-content hash in their tenant-hashed key. Every write uses one `PutObject` with `If-None-Match: *`. A write timeout/collision is reconciled with a bounded GET and decryption/hash verification; no unconditional overwrite, delete, multipart replacement or inference retry exists.
3. An Ed25519-signed commit at the next original event sequence pins the segment hash, original event predecessor/head, preceding signed-manifest hash, tenant and archive identity. Competing exporters adopt only an identical source prefix. The worker verifies the winning object and commit before advancing.
4. A local cursor is atomically fsynced on a dedicated persistent volume after remote verification. A process/file lock and Kubernetes `concurrencyPolicy: Forbid` serialize the shared cursor. Losing the local cursor recovers through existing remote commits; a crash after a segment upload or a remote commit does not duplicate events.
5. A retained local cursor detects a removed/replaced previously committed remote tail. The owner copies each successful checkpoint and its signer public key to an **independently controlled backup**. Without this independent expected head, a deleted remote chain tail can look internally consistent. The restore CLI requires the exact independently trusted checkpoint and verifies both manifest and original event chains.

The object store must have versioning and bucket-default **COMPLIANCE** retention enabled before writes. The writer has GET, GET-retention and conditional PUT only on its tenant prefix. It cannot change retention, bucket policy, legal holds or delete objects. Every new object is read back and its actual retention is checked; missing/short/non-COMPLIANCE retention stops the run. This is distinct from provider-generated response idempotency: the archive retries durable evidence, never model calls.

## Operator setup

The reviewed source files are under `deploy/archive`. `kubernetes.example.yaml` is a private, suspended **single-node pilot**, not a high-availability promise. It uses retained PVCs, nonroot pods, no service-account API tokens, no public/NodePort/Ingress service, TLS, bounded resources, default-deny network policy and a one-minute exporter schedule. Replace the storage/worker image digests, actual encrypted StorageClass, private database route, tenant configuration and private mounts before applying it. The documentation-only DB address deliberately provides no working egress. A network-policy-capable CNI and private connectivity from the worker to the existing RDS database are prerequisites.

Use the upstream [Helm production setup](https://github.com/seaweedfs/seaweedfs#kubernetes-with-helm) when fault tolerance is required: multiple masters, separate volume failure domains, replicated object data, and an appropriate shared/replicated filer metadata store. Merely increasing the all-in-one StatefulSet's replica count does not create a correct distributed store. Set PV reclaim policy to Retain and encrypt the underlying disks and backups; protect filer metadata and all volume data together. Configure audited S3 access logs, restricted operator access and storage/worker health/lag alerts. Keep retention administration, archive-read authority, signing keys and database-owner credentials separate.

Mount these private inputs via your established secret manager, never commit them:

- `archive-storage-tls`: certificate/key with the exact private service DNS SAN. Trust its CA explicitly; never disable verification.
- `archive-storage-identities`: `s3.json` with SeaweedFS static identities and attached JSON policies. `scripts/prepare_archive_storage_config.py` renders this privately from separate writer/reader credential files and the reviewed example policies. It supplies no administrator/anonymous identity and no broad legacy Write action. Initialize the bucket first with a temporary, private operator-only configuration, enable versioning/Object Lock and default COMPLIANCE retention, then replace that bootstrap configuration with the rendered file and restart storage. Retain the bootstrap/change receipt independently. The final pod's IAM API is read-only.
- `academy-audit-export-config`: `config.json` based on `config.example.json`. Set `prefix` to `ncacademy/` plus SHA256 of the exact Core organization ID. Endpoints are operator-only exact HTTPS origins on an explicit allowlist. The SDK also rejects changes in origin/prefix/method before every request, including redirected requests, and ignores ambient proxy configuration.
- `academy-audit-writer`: `credentials.json` with exactly `access_key_id` and `secret_access_key`; these are archive-only credentials. There is no ambient AWS credential discovery.
- `academy-audit-keys`: 32-byte `encryption.bin`, raw 32-byte Ed25519 `signing-private.bin`, and its raw 32-byte `signing-public.bin`. Generate using a cryptographic RNG/library, retain an independent encrypted recovery copy and pin the public-key fingerprint outside the cluster. This format fixes one deterministic tenant prefix per bucket. Key rotation therefore requires a new private bucket/configuration, a newly verified full-chain export, and an independently retained link to the old checkpoint; retain old keys/evidence for their approved lifecycle. Changing key files in place cannot resume the old cursor.
- `academy-audit-database-reader`: `url.txt` for a separate login following `archive-reader-role.sql`, with `sslmode=verify-full` and mounted `sslrootcert`. The worker rejects elevated role attributes and any audit-table write privilege. Do not reuse the API or migration login.
- `academy-audit-trusted-ca`: `ca.crt` for storage plus the RDS CA file referenced in the database URL.

Build the separate worker from the repository root:

```sh
docker build -f apps/archive/Dockerfile -t ncacademy-archive:review .
```

Its dependencies are separately locked and are not installed in the API container. The API's database/bootstrap/identity secrets and absence of a task role remain unchanged. The Kubernetes Secrets must use encrypted control-plane storage and narrowly scoped RBAC; Secrets base64 encoding is not encryption. Run the worker as a Job manually until preflight and recovery tests pass, then unsuspend the schedule.

The explicit local contract commands create only uniquely named disposable containers, synthetic records and private temporary files. They do not contact AWS or existing student storage:

```sh
python -m pip install -r apps/archive/requirements.lock.txt
python scripts/smoke_archive_store.py --run
python scripts/smoke_archive_database.py --run
```

The same functional contracts can run without Docker using installed PostgreSQL (`--run --native`) or an explicitly supplied, hash-verified upstream SeaweedFS binary (`--run --native-binary /private/review/weed`). Native storage binds all components to loopback, uses random ports/private configuration, removes ambient AWS/storage environment credentials, and runs only as an unprivileged caller. This verifies the S3 protocol and database boundary; it does not establish Kubernetes/PV admission. The reviewed upstream Darwin ARM64 4.48 release archive has SHA256 `faa194e72a346755dc4404288b0561eb3c260351702901d8363024d5099bdd7d`; verify the appropriate platform's asset against its own [official release metadata](https://github.com/seaweedfs/seaweedfs/releases/tag/4.48) before execution. No binary is included in source packages.

## Owner review and recovery

Normal current-authority session review remains available in Academy. Full external tenant reconstruction is an owner/operator action, using a separate read-only storage credential and private decryption key. No browser receives object-store credentials, presigned archive URLs, encryption keys or another student's sessions. Existing School/Teacher replay remains class/school scoped; do not distribute this full-tenant CLI credential to school staff.

```sh
python apps/archive/academy_archive.py restore \
  --config /private/archive/config.json \
  --credentials /private/archive/reader-credentials.json \
  --encryption-key /private/archive/encryption.bin \
  --public-key /private/archive/signing-public.bin \
  --trusted-checkpoint /independent-backup/accepted-cursor.json \
  --output /private/review/academy-audit.json
python scripts/verify_audit.py /private/review/academy-audit.json \
  --checkpoint /independent-backup/event-head.json
```

`accepted-cursor.json` is the exact successful worker `checkpoint` object copied independently. `event-head.json` contains its `tenant_id`, `sequence` and `digest`, obtained from the same independent receipt. Restore reconstructs that precise accepted snapshot even if newer commits arrive during review. It refuses to replace an existing file, writes private mode 0600, verifies the expected head and reports the output SHA256 without printing student content. Log this owner's credential use through the storage access log and retain the local access/restore receipt independently.

Successful jobs emit only `archive_current`, timestamp, sequence/lag and the trusted-head candidate. Failures emit `archive_failed`, return nonzero and do not expose SDK/SQL exceptions or student text. Alert if a job fails or no successful checkpoint arrives within three scheduled intervals; independently compare the current database head and archived sequence after an outage. A one-minute schedule is a target exposure window, not a guarantee: job duration, failures and operator response can increase it. Full-chain verification each run is deliberately conservative and must be benchmarked before large deployments.

Do not enable real student admission until the deployed endpoint has passed synthetic conditional-create, duplicate/crash recovery, TLS/anonymous refusal, enforced retention/delete refusal, archive read isolation, tamper/truncation and independently trusted restore tests. Copy encrypted evidence and checkpoint/access receipts to a separate administrative/storage failure domain. Schedule complete archive verification/restoration regularly: the incremental exporter checks its retained boundary and new commits, whereas owner restore verifies every preceding manifest/segment. Test restoring both metadata and volume backups. Object Lock preserves retained versions; it does not by itself prohibit creating a newer version at the same key. This worker always uses conditional creates, and independent signatures/checkpoints detect replacement; tighten bucket header policies only after testing their exact enforcement on the selected store. Kubernetes/disk/cluster administrators can still remove volumes, secrets or the entire store; local Object Lock and a signature are not protection from that administrator. No automated purge, legal-hold workflow or universally appropriate retention duration is implied by the 365-day example.
