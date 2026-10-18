# Verification record

Updated October 17, 2026 for the authorized Core deployment. Local checks below establish tested behavior; public DNS, identity, archive and deployed-service acceptance must be verified separately.

## Checks completed

| Check | Result |
| --- | --- |
| API/security and script tests | 63 passed, including real isolated local PostgreSQL runtime-role checks. Ordinary Core/provider tests use synthetic records and mocked transports. |
| Frontend tests and production build | 11 tests passed; TypeScript and Vite production build passed. |
| Core onboarding patch | 129 offline checks passed: 113 Core cloud/identity/deployment checks and 16 GitHub gate checks. Migration consistency and patch reapplication passed. See the [baseline record](../nce/core-patch-baseline.json). |
| Restricted database runtime | Real PostgreSQL API login, course creation, audit export and logout passed with runtime credentials. Audit-event UPDATE/DELETE/TRUNCATE, head deletion/truncation, schema changes, temporary tables and role creation were denied by database permissions. Migration reruns and quoted-password rotation passed; owner/elevated-role reuse was refused. |
| Migration privilege boundary | Repeated database-role verification with a nonsuperuser database owner possessing CREATEROLE; no superuser-only runtime provisioning was required. The temporary synthetic test database/container was removed. |
| Container package | Local ARM64 API and web images built; explicit migration and API/web/PostgreSQL health checks passed. Production uses the digest-pinned API image in the release manifest. |
| PostgreSQL coursework workflow | 11 synthetic smoke checks passed: enrollment isolation, unreleased-bank privacy, attempt gating, approved-text release, idempotency, turn limit, immutable submissions, role boundaries, feedback, replay evidence and full-chain verification. |
| Browser workflow | Mobile student attempt → approved guidance → submission; desktop instructor replay shows attempt, request, released hint and saved/submitted work history. |
| Core infrastructure validation | CloudFormation lint, platform invariants and reviewed-source secret scan passed; API and migration database secrets/execution roles are distinct. |

The Python suite reports one third-party AnyIO BlockingPortal deprecation warning. No paid inference ran. Local Compose records are synthetic and separate from production. Privilege tests used an additional disposable loopback PostgreSQL database, not the Compose application database.

The optional PostgreSQL privilege test runs only when `ACADEMY_TEST_POSTGRES_URL` identifies an isolated loopback database named `academy_privilege_test`; it refuses other databases. With that input, `PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests scripts/tests -q` exercised all 63 tests. Without it, that integration test is skipped. Do not point it at application data.

The offline verifier detects changed contents, foreign tenant heads and deleted tails relative to a supplied trusted head. Synthetic test checkpoints establish algorithm behavior; deployed independent retention and KMS-signature verification require separate evidence.

## Deployment acceptance

- Confirm passing CI for the exact published application commit, the manifest's ECR digest and the released trusted Core recipe/runner revision.
- Verify `ncacademy.app` and `api.ncacademy.app` DNS/TLS, Amplify `/api` proxy, API readiness, canonical callback and exact Origin/CSRF checks. Keep the existing Core issuer unchanged.
- Sign in as the owner with explicit `admin`, `academy-use` and production environment access. Verify current Core revocation, absence of demo login, and denial of non-owner/student access while student grants remain unissued.
- Confirm ECS API secret references contain only the runtime database identity and that API code has no AWS task role. Verify deployed privilege restrictions without altering audit records.
- Retain an operator export and KMS-signed checkpoint in the independent encrypted Object Lock archive. Verify signature, checksum, retention/object versions, authorized archive access and tamper/truncation detection; perform synthetic backup/restore verification. Assign checkpoint cadence and missed-checkpoint monitoring before admitting students.
- Review student/instructor notices, retention/hold/deletion rules and teacher-authored hint banks before real enrollment. Use synthetic records to verify student/instructor/admin combinations and course isolation.
- Keep `ALLOW_LIVE_MODELS=false`. A later provider acceptance requires approved endpoint/model/key/processing terms and an explicit bounded live-call budget; mocked tests do not validate a provider's live protocol.

This record does not assert public deployment completion, educational/privacy certification, independent authorship or a provider's zero-retention posture.

## Prepared issuer-binding upgrade

The isolated authority-binding patch passed 87 API/script tests with one optional PostgreSQL integration skip, plus source-package validation. Tests include signature-validated mocked fresh/legacy/migrated login, both-issuer configuration rollback, immutable owner/coursework and audit preservation, complete inventory and tenant rejection, duplicate identities, first-apply cleanup, idempotent replay and injected transaction rollback. Runtime/Core/model transports remained mocked. An independent reviewer additionally passed 20 synthetic PostgreSQL 16.15 checks covering schema/grants, operator/runtime separation, alias backfill, preserved ownership/audit, collisions, foreign tenants and transaction rollback, then three targeted PostgreSQL checks after the final premature-issuer guard. Production DNS, release/image/CI and sign-in acceptance remain pending.

The reviewed identity upgrade was built as an ARM64 API image with the locked dependencies and pinned by immutable ECR digest in the source manifest. Its application modules compile in the resulting nonroot image and match the reviewed source bytes. This verifies image construction; production schema/backfill, issuer and application sign-in acceptance remain separate operator steps.
