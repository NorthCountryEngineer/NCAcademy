# Verification record

Updated October 18, 2026 for local school-permissions development. Historical validation below records the preceding releases. It does not establish acceptance of this new revision. Public DNS, identity, archive and deployed-service acceptance must be verified separately.

## School-permissions acceptance

Verify the four roles against at least two synthetic schools and multiple classes. A School administrator in one school must not administer/read another school; a Teacher maintains assigned classes and uses permitted school resources; a Student sees enrolled coursework and own attempts/submissions only. Include a principal with different local roles in two schools so a higher role in one cannot leak into the other. Hiding controls in the frontend is not an authorization test.

Verify Core capability removal, local membership/placement/enrollment revocation, disabled principals, cross-tenant/object IDs, pending agent content privacy, human approval/rejection, stale-target and changed-placement conflicts, immutable submissions and stable legacy owner/issuer bindings. School/session exports must be explicitly partial and omit other scopes; only Admin can export/verify the full tenant chain. Compare historical audit bytes/hashes before and after the additive migration and repeat migration without resetting later grants.

Agent tests use mocked Core responses only. Reject wrong issuer/audience/organization/account/binding/consumer/environment/task, malformed/expired claims, missing operation permissions, unknown resources, expired tasks, changed local approvals, mixed human cookies/Bearer input and Core outages. Verify workload Bearer JSON introspection is separate from human Basic introspection; no incoming headers choose trusted task/purpose/environment. No credentials enter responses, prompts, audit data or browser variables.

The optional Core recipe extension must remain disabled by default. Verify all four independent model/agent flag combinations, conditional API-only workload-secret injection, migration agent-disable/no-token, existing IAM/secret references unchanged, exact patch application, CloudFormation syntax and applicable offline security rules. No test or health probe invokes a live model. Final test counts for this revision belong in its local release report after the complete suites pass.

Local acceptance on October 18 passed 153 API/script tests, including both real isolated PostgreSQL checks, and 25 frontend tests. TypeScript/Vite and nonroot API/web container builds passed. An isolated PostgreSQL/API/web container workflow verified School isolation, class assignment, student enrollment, manual draft/submission, scoped session evidence and denial of full tenant export to School. Real agent authentication and revocation were exercised through the HTTP API with mocked Core authority. Separate synthetic browser checks covered contextual roles, agent placements and publication review; screenshots and receipts are retained locally under `build/verification/`. No live model, production change or public release ran. The final exact suite count and local image IDs are recorded in that receipt directory.

## Historical checks completed before school permissions

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
- Verify `ncacademy.app` and `api.ncacademy.app` DNS/TLS, Amplify `/api` proxy, API readiness, canonical callback and exact Origin/CSRF checks. Preserve canonical Core issuer `https://core.nnytech.ai` and existing stable human identity aliases.
- Sign in as the owner with explicit `admin`, `academy-use` and production environment access. Verify current Core revocation, absence of demo login, and denial of non-owner/student access while student grants remain unissued.
- Confirm ECS API secret references contain only the runtime database identity and that API code has no AWS task role. Verify deployed privilege restrictions without altering audit records.
- Retain an operator export and KMS-signed checkpoint in the independent encrypted Object Lock archive. Verify signature, checksum, retention/object versions, authorized archive access and tamper/truncation detection; perform synthetic backup/restore verification. Assign checkpoint cadence and missed-checkpoint monitoring before admitting students.
- Review student/instructor notices, retention/hold/deletion rules and teacher-authored hint banks before real enrollment. Use synthetic records to verify student/instructor/admin combinations and course isolation.
- Keep `ALLOW_LIVE_MODELS=false`. A later provider acceptance requires approved endpoint/model/key/processing terms and an explicit bounded live-call budget; mocked tests do not validate a provider's live protocol.

This record does not assert public deployment completion, educational/privacy certification, independent authorship or a provider's zero-retention posture.

## Prepared issuer-binding upgrade

The isolated authority-binding patch passed 87 API/script tests with one optional PostgreSQL integration skip, plus source-package validation. Tests include signature-validated mocked fresh/legacy/migrated login, both-issuer configuration rollback, immutable owner/coursework and audit preservation, complete inventory and tenant rejection, duplicate identities, first-apply cleanup, idempotent replay and injected transaction rollback. Runtime/Core/model transports remained mocked. An independent reviewer additionally passed 20 synthetic PostgreSQL 16.15 checks covering schema/grants, operator/runtime separation, alias backfill, preserved ownership/audit, collisions, foreign tenants and transaction rollback, then three targeted PostgreSQL checks after the final premature-issuer guard. Production DNS, release/image/CI and sign-in acceptance remain pending.

The reviewed identity upgrade was built as an ARM64 API image with the locked dependencies and pinned by immutable ECR digest in the source manifest. Its application modules compile in the resulting nonroot image and match the reviewed source bytes. This verifies image construction; production schema/backfill, issuer and application sign-in acceptance remain separate operator steps.


## Supplemental school/agent Core patch validation

The separate patch targets reviewed Core `79e9d5284c087fbcce67eb34b3efcfe46f6fbfa6`; its hash and scope are in [school-agent-patch-baseline.json](../nce/school-agent-patch-baseline.json). The final artifact passed hash verification and clean-export apply/reverse checks, 115 offline Core cloud/recipe/registration checks, 16 GitHub CI gate checks and migration consistency. Tests ran in the installed Core test image with networking disabled. No AWS, production Core or model operation ran.

Existing local `cfn-lint` passed the modified fixed Academy template. Existing `guardpycfn` passed its applicable private-database and private-Fargate rules; bucket/browser/ECR/runtime-secret-creation rules were not applicable to this template. The template grew from 28,942 to 30,051 bytes, below the inline CloudFormation limit. Every resource except the two task definitions compares unchanged after JSON parsing, including IAM roles/policies, secret references, network and retained database. Only task environment/API conditional-secret wiring and contextual metadata changed. Account-aware service pre-deployment validation was not run because this work is local preparation.

The subsequent Core upgrade compatibility review confirmed the canonical public discovery document and obtained the upgrade workflow's fresh read-only AWS confirmation of the deployed Core/runner baseline. It identified a School permission display-name mismatch between the simulated registration and the Academy manifest. The corrected supplemental artifact asserts all four complete permission definitions in order and again passed clean apply/reverse checks, 115 Core checks, 16 gate checks and migration consistency. Infrastructure template bytes are unchanged, so the preceding lint/Guard evidence remains applicable. Core's domain upgrade did not adopt this supplement or upgrade the Academy installation's permission contract. The [deployment review](deployment.md#core-upgrade-compatibility-review--october-18-2026) records the remaining adoption steps.
