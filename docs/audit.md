# Session evidence and audit controls

## What instructors can establish

Replay preserves the student's submitted attempt and questions, the assistance actually displayed, assignment/policy/model snapshots, denials, provider errors and usage. Instructors can inspect progress over time and compare the student's final submission with the assistance supplied. It records service-visible evidence; it cannot prove independent authorship, record work done elsewhere, or expose hidden model reasoning.

## Event obligations

Record UTC timestamps and strictly ordered events, tenant/course/student/session and operation IDs, actor, event kind and canonical payload. Include immutable assignment/policy/hint-bank and model connection versions. Each record incorporates its predecessor hash. Log tutor-off decisions, missing-attempt blocks, exhausted limits, malformed/withheld output, uncertain sends, failed providers, successful release, replay reads and exports. No API permits audit edit/delete. Never place credentials or authentication tokens in events.

Write request/send evidence before network I/O and completion/usage/output evidence before returning content. An audit failure prevents assistance. A process exit after send can leave an uncertain request; its idempotency key must never trigger an automatic replay. Administrators investigate unresolved pending operations instead of resetting them for replay. A completed withheld outcome keeps its reservation; the same idempotency key never sends again. A new explicit user request may use remaining allowance. Do not run background retry loops for provider traffic.

## Tamper evidence and production retention

Application append-only writes and a hash chain provide evidence of modification relative to a trusted head. A hash chain stored beside the data does not resist a privileged database rewrite, deletion of a chain tail, or replacement of the database. The production API role does not own the schema and receives only SELECT/INSERT on audit events; UPDATE/DELETE/TRUNCATE and schema DDL are denied. The mutable audit head permits SELECT/INSERT/UPDATE to advance the chain. A separate owner-only migration identity creates schema, triggers and runtime grants. SQLite and local Compose owner credentials are not a production audit boundary.

The production retention design uses an independently controlled encrypted S3 bucket with Object Lock retention and operator-run KMS-signed checkpoints. Capture the tenant ID, sequence/count, last event hash, timestamp and exact export SHA256 in signed checkpoint material. Retain the export, checkpoint material, signature, key/algorithm metadata and object-version identifiers together outside the database trust boundary. Verify the KMS signature and export checksum before trusting the head.

The application has no AWS task role, archive-write permission or KMS signing permission. A separate operator performs export, signing and retained upload; this release has no automatic checkpoint worker. An initial migration checkpoint alone does not retain later student activity. Before admitting real students, verify the deployed bucket's retention configuration, key/archive authority separation, checkpoint cadence, monitoring and restore path. Do not label an unanchored database WORM/immutable or treat a signed but unretained checkpoint as a complete retention control.

Choose checkpoint cadence and retention based on accepted loss/exposure windows; assign an operator until an independently reviewed automation exists. Monitor missing checkpoints and unresolved send markers. Exercise verification against both altered content and deleted tails, then restore a synthetic export and verify its trusted head before production acceptance. Keep signer/deletion authority separate from application/database credentials. Record reviewer access to external archives in the archive access log as well as the application log.

## Access, privacy and lifecycle

Full replay belongs to assigned instructors and explicitly authorized organization administrators; students see their own permitted conversation. Scope every search/export by tenant and course. Revoke reviewer access through Core and local course membership. Exports contain student records: store them in restricted operator storage, never Git, public docs or build artifacts.

There is no automatic audit purge in the initial package. Define record retention, legal holds, backup expiration and an approved deletion process before using real records. Expiring a local session must not silently delete its evidence. The production runbook must include breach/access response, authorized record export and recovery ownership.
