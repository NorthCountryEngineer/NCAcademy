# Homework conversation audit delivery and automatic archive operations

NCAcademy retains private student questions, photographs and exact displayed responses under its own access and encryption controls. Core Data receives restricted operational metadata through a durable database outbox. A separate encrypted archive retains the original audit chain for owner review. Neither pipeline delivery nor an archive retry invokes a teaching model.

The source package includes both scheduled workers. Production operation requires deployed credentials, routes, storage retention and independent checkpoint monitoring; adding the source files does not activate either schedule.

## Evidence saved with each interaction

The API appends its encrypted audit event and the corresponding `audit_delivery_outbox` entry in the same database transaction. An unsuccessful transaction retains neither. A committed interaction remains queued even when Core is unavailable, the API restarts or the dispatcher stops. Delivery does not block the student's response after the local evidence has committed.

The outbox contains a strict metadata schema: audit sequence and ciphertext hash, predecessor hash, occurrence time, internal event type, tenant and event references, and available session, school, class, teacher and operation references. References are tenant-salted SHA256 pseudonyms. They support correlation but remain sensitive operational metadata; pseudonymization does not make them anonymous. No student or actor identity reference is exported. Provider phase is limited to `triage`, `tutor` or `safety`; states use a fixed enum. Usage contains bounded integer token counts. Approved model, policy, configuration, grounding and other admitted snapshots contribute hashes only.

Questions, replies, names, biographies, photographs, audio, private instructions, URLs, account credentials and hidden model reasoning are absent from this transport. The dispatcher independently checks the serialized metadata schema, its checksum, tenant, event identity and school scope before sending. It cannot read the raw audit or application records tables and never mounts the PII key.

Original audit ciphertext, photographs and authorized private replay remain in Academy. Outbox and delivery receipts are append-only at the ORM and database layers. These controls and hash chains do not prevent a database owner from rewriting its database; independently retained archive checkpoints provide the separate comparison point.

## Core Data contract

The current Core implementation uses:

```http
POST /api/platform/v1/pipelines/academy-audit-metadata/events/
Authorization: Bearer <separate workload credential>
X-Core-Purpose: academy-audit-metadata
Content-Type: application/json

{"event_id":"academy-audit-<64 hexadecimal characters>","data":{}}
```

The worker supplies the complete validated metadata object as `data`. Its stable event ID derives from the deployment tenant and original audit event ID. Core returns `201` and `{"record_id":17,"created":true}` for a new dataset event, or `200` with the same record ID and `created:false` for an identical duplicate. Reusing that dataset event ID with different data returns `409`. Core checks the live workload credential, `data.write` capability and pipeline `data.ingest` authority on each publication. The integration preflight also checks the configured organization, installation service, environment and capability.

This contract is implemented in Core's `platform_services/api.py`, `platform_services/data.py` and `platform_services/tests/test_platform.py`. Create a dedicated restricted dataset using [the supplied schema](../deploy/archive/core-metadata-dataset.schema.json), with a dedicated active pipeline and **no transforms**. Pin that pipeline and dataset configuration through the normal Core administrative change process. The ingest response confirms the record ID and idempotency result; it does not return a stored-content digest. Independently inspect synthetic records before activation to verify that Core retained all metadata unchanged.

Give the separate production workload credential only the required data capability and authority for this pipeline. Do not reuse a student identity, agent credential, human bearer token or the API's Core credential. Dataset visibility and retention belong to the restricted operational monitoring role; they do not grant transcript access or replace Academy enrollment checks.

## Delivery leases and recovery

`audit_delivery_state` holds delivery state independently of immutable evidence. A PostgreSQL row lock with `SKIP LOCKED` admits one worker per event and commits a unique lease token before network I/O. SQLite uses an immediate transaction only for synthetic tests. A crashed worker's bounded lease expires; another worker records an append-only `lease_expired` receipt before retrying that same event ID. The first process cannot overwrite a newer lease owner's state or receipts.

On confirmed creation or duplicate acceptance, the dispatcher updates state and appends a delivery receipt atomically. A timeout or invalid response retains a bounded uncertain outcome and exponential backoff, capped at one hour. A commit followed by a lost response is safely reconciled by Core's identical-event check. No response body or exception diagnostic enters the receipt.

Authority denial, missing pipeline, idempotency conflict and permanent rejection block that event and stop the run. Restore authority or reconcile the conflict before an explicit `--resume-blocked` run; scheduled runs never silently clear those blocks. A different origin, organization, service, environment or pipeline cannot resume an event already pinned to a destination. Configuration migration requires an owner-reviewed plan that preserves existing dataset event IDs and receipts.

The HTTP client uses an exact allowlisted HTTPS origin, fixed operation paths, mounted CA trust, five-second connection and fifteen-second response timeouts, and a 16 KiB response limit. It does not discover ambient credentials, honor proxy environment variables, follow redirects, call arbitrary URLs or retry within the HTTP request.

## Install the metadata worker

1. Deploy the API schema migration first using the separate owner identity. Schema 11 creates outbox, state and receipt tables and constructs structural metadata for historical audit events without changing their ciphertext or original hashes. Every explicit owner migration also reconciles any missing outbox entries without replacing existing richer metadata. Quiesce old API writes for cutover, or run final owner reconciliation after all old tasks stop; a late old task can otherwise append beyond the last sweep. Encrypted historical payloads contribute structural fields only; private school/model/usage fields are not decrypted for backfill. Global administrators can inspect this backlog; school status contains only events carrying that school's metadata reference.
2. Run [core-delivery-role.sql](../deploy/archive/core-delivery-role.sql) in the dedicated Academy database and provision its password privately. The dispatcher receives SELECT on the three metadata tables, UPDATE on delivery state and INSERT on receipts. It receives no privileges on raw audit events, student records, secrets, schema creation or role memberships. The worker checks its actual database privileges before admission. Its PostgreSQL URL must use `sslmode=verify-full` and a mounted CA file. The API can append outbox/state rows and read receipts but cannot mark its own events delivered.
3. Create the dedicated Core dataset, unchanged-data pipeline and restricted workload credential. Use [the example worker configuration](../deploy/archive/core-delivery-config.example.json), replacing the organization, service, environment, exact Core origin and CA path. Mount the credential as a private JSON file with exactly `{"token":"..."}`. Keep database and Core credentials in separate secret mounts; none belongs in Git or a command argument.
4. Build `apps/archive/Dockerfile` and pin its immutable image digest. Configure [core-delivery.example.yaml](../deploy/archive/core-delivery.example.yaml) with the existing private archive namespace and service account. Replace documentation-only egress addresses with reviewed PostgreSQL and Core routes. The dispatcher has no public service, Kubernetes API token, archive decryption/signing keys or cloud identity.
5. Run a manual synthetic Job with the schedule suspended. Verify creation, an identical duplicate after a simulated lost response, a crash after lease admission, revoked authority, foreign-tenant refusal, changed-data conflict, empty proxies, TLS refusal and exact stored Core metadata. Preserve acceptance receipts privately. Then enable the one-minute schedule through the normal deployment change process.

The separate container commands are:

```sh
python /archive/core_delivery.py deliver \
  --config /delivery-config/config.json \
  --credentials /core-workload/credentials.json \
  --database-url-file /delivery-database/url.txt

python /archive/core_delivery.py health \
  --config /delivery-config/config.json \
  --database-url-file /delivery-database/url.txt
```

The `health` command performs no Core request. Its database connection still requires the restricted dispatcher role. Health output contains status counts, the oldest pending age and last successful delivery time. It returns nonzero for blocked events or a backlog older than three minutes. Alert on job failures, revoked preflight authority, lease recovery bursts and sustained pending age. Size batch capacity and the schedule for observed audit traffic; the fifty-event example batch is not a throughput guarantee. Administrative status uses current application authorization and school scope, and records reviewer access in the original audit chain.

## Operate the automatic private archive

The [archive package](session-archive.md) preserves original audit ciphertext in encrypted, signed, conditional-create S3-compatible segments. Its database identity remains SELECT-only on audit events and heads; it does not use metadata dispatcher grants. The private SeaweedFS pilot requires actual persistent storage, TLS, versioning, bucket-default COMPLIANCE retention, independent backup and restore acceptance before activation.

The scheduled exporter now mounts an **independently maintained, read-only accepted checkpoint** at `/independent/accepted-cursor.json`. It verifies that expected remote manifest history and its original database boundary before appending. A removed remote tail or a database restoration behind this checkpoint fails even if the local cursor volume was lost. For an empty new archive, an owner may initially accept the exact `Archive.empty()` checkpoint only after proving no earlier archive exists. An existing archive must resume from its independently retained accepted checkpoint; generating a new genesis value is not recovery.

The exporter writes a durable `/state/health.json` only after a verified successful export. Monitor it with:

```sh
python /archive/archive_health.py \
  --health-file /state/health.json --max-age-seconds 180
```

This freshness probe reads no student content and makes no network request. It reports only success age, verified sequence and lag; a receipt older than three minutes, an incomplete export or a future timestamp fails. Collect failed Job status independently so a recent earlier receipt cannot conceal a new integrity failure. After successful runs, the independently controlled monitoring/backup process must retain and advance the accepted checkpoint and signer public-key fingerprint. The exporter cannot update that mount. Alert on missed success reports and on failure of the independent checkpoint copy. Copying a receipt to another path on the same worker volume does not create independent retention.

Keep the archive CronJob suspended until endpoint admission, enforced retention, synthetic duplicate/crash recovery, independent-head continuity, backup restoration, TLS and private-read isolation pass. Automatic export, independent checkpoint collection and monitoring must all be operational before real student admission. Core metadata monitoring and archive health are separate signals: a delivered Core record never proves that its full private session has reached the independent archive.

## Local verification

All tests use synthetic records and mocked Core/storage transports. Run:

```sh
PYTHONPATH=apps/api:apps/archive .venv/bin/python -m pytest \
  apps/api/tests/test_audit_delivery.py apps/archive/tests -q
```

The suite covers same-transaction rollback, metadata allowlists, immutable SQL evidence, school isolation, current-authority checks, exact Core requests and receipts, lease recovery, stale ownership, timeout-after-commit reconciliation, fixed destinations, no redirects, TLS configuration, archive checkpoint loss and health freshness. PostgreSQL privilege and S3 retention acceptance still use the separate synthetic operational contracts described in the archive runbook.
