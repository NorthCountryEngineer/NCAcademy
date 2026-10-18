# Operations and recovery

## Local installation

Run `python3 scripts/setup_local.py` once to create ignored local secrets, then `docker compose up --build -d`. Open `http://localhost:8090`. Compose uses project `ncacademy-local`, dedicated PostgreSQL storage and loopback-only frontend port 8090. The API/database are not published to the host. Other local Proposal Studio/Core containers and volumes are not part of this project.

Inspect status with `docker compose ps` and health with `docker compose exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/ready').status)"`. View service logs with `docker compose logs --tail 100 api`. Keep student/provider payloads out of logs. `docker compose down` stops the local service while retaining its database. Removing the named database volume is a deliberate destructive reset, not an upgrade step.

Local roles are synthetic demonstration identities selected at sign-in. Courses, policies and work you create in that mode are still local records; never put real student records into a source screenshot or archive.

## Initial production access

Use `https://ncacademy.app`; its `/api` requests proxy to the protected backend at `https://api.ncacademy.app`. Identity remains at `https://main.dpn5tiolbymzn.amplifyapp.com`. Grant the owner both Core `admin` and `academy-use` plus production environment access. A valid first sign-in creates the local app user; an empty initial user list is expected. No production demo users are seeded.

Keep live models disabled and issue no student grants until independent retained audit verification and access acceptance are complete. API readiness establishes schema/storage availability only. Database owner credentials belong solely to migration; the API receives a restricted runtime login and has no AWS task role. The migration bootstrap inputs are `ACADEMY_RUNTIME_DB_USERNAME` and `ACADEMY_RUNTIME_DB_PASSWORD`, injected from the runtime database secret and never printed.

## Instructor workflow

Create a course, enroll the relevant student identities, add assignments and choose a module grouping. Write the assignment prompt and a small vetted hint/question bank. Leave assistance off for independent assessments; select curated assistance for deterministic stored hints or Socratic selection when an approved provider is enabled. Students write/save their attempts and submit their own work. Instructors review immutable submissions and session replay.

Review assistance chronologically, comparing attempt snapshots and released hints with the final submitted version. Review blocks/errors as well as successful hints. Do not interpret a provider selection or service transcript as proof of the student's unseen reasoning.

## Administrator workflow

Use Administrative settings for organization/member status, named model connections, enabled model choices and application/audit configuration status. Enter a replacement key only when replacing it, or explicitly clear the saved key. The API never returns saved secrets. Configure authoritative production roles in Core; course enrollment is an additional application boundary. Changing client/tenant/domain bindings requires an explicit registration/release process.

## Audit export verification

An administrator exports the complete tenant audit chain through the authenticated admin export endpoint. Session-only views contain selected events and cannot prove continuity of the entire tenant chain independently. Store exports outside the source tree in restricted operator storage.

```sh
python3 scripts/verify_audit.py /PRIVATE_OPERATOR_PATH/audit-export.json
python3 scripts/verify_audit.py /PRIVATE_OPERATOR_PATH/audit-export.json \
  --checkpoint /INDEPENDENT_RETAINED_PATH/trusted-checkpoint.json
```

The verifier checkpoint has `tenant_id`, `sequence` and `digest`, obtained from a separate trusted retention boundary. Without it, the verifier reports internal consistency only. It detects changed event contents/order, tenant mismatches and a truncated chain relative to the trusted head. The CLI does not verify KMS signatures; authenticate the independently retained signed checkpoint first, then supply its trusted head to this verifier.

For an operator checkpoint, preserve the exact exported bytes and SHA256, tenant/head, UTC timestamp, KMS signing key and algorithm metadata, signature and retained S3 object versions. Verify the signature and exported-byte checksum before chain verification. Retain export and signed checkpoint in the independent Object Lock archive using separate operator authority. The API cannot write the archive or sign its own head. This release has no automatic checkpoint worker: establish a documented cadence, missed-checkpoint alert and recovery owner before student admission. Archive access must be logged independently as well as through application export events.

Exercise changed event content and deleted-tail detection against the retained head, then restore a synthetic export/backup into an isolated database and verify it. An initial signed schema checkpoint is useful deployment evidence but does not cover later student sessions.

## Monitoring and recovery

Monitor API readiness, Core introspection failures, expired sign-ins, denied assistance, unresolved sends, database storage/backup status and independently retained checkpoint age. Readiness verifies application storage/schema; it does not establish provider availability or current Core permissions. Avoid health probes that consume model tokens.

Back up PostgreSQL and retain its matching encryption bootstrap separately. Encrypt backups and restrict restore/export access. Test restore on a new isolated database using synthetic records and verify the externally trusted audit head before production acceptance. A lost encryption bootstrap makes stored credentials/tokens unavailable; replacing it is not a transparent rotation.

On Core/provider outage, keep coursework evidence intact and retry only a user-visible operation known not to have sent inference. An uncertain send stays uncertain and does not automatically incur a second call. Operator incident response and retention/deletion procedures must be agreed before real student enrollment.
