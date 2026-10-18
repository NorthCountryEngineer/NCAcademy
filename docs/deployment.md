# Deployment requirements

## Release scope and addresses

The existing service uses `NorthCountryEngineer/NCAcademy` and deploys through NCE Core. The canonical frontend is `https://ncacademy.app`; the protected backend origin is `https://api.ncacademy.app`. Core's canonical issuer is `https://core.nnytech.ai`. This school-permissions revision is prepared locally. Its source, supplemental Core patch and configuration do not change the running service until a separate reviewed release. Public deployment completion must be established by acceptance checks, not inferred from source preparation or image publication.

NCAcademy uses the dedicated trusted `amplify-academy-fargate` recipe. The original `amplify-fargate` recipe starts Proposal Studio-specific worker/browser commands and cannot start this application unchanged. [Core onboarding](../nce/README.md) and its baseline record identify the reviewed recipe, repository CI policy and validation.

## Core upgrade compatibility review — October 18, 2026

The completed canonical-domain upgrade is compatible with this application. Public discovery advertises `https://core.nnytech.ai` and its canonical authorization, token and signing-key endpoints. The upgrade workflow's fresh read-only AWS checks confirmed Core and its trusted runner remain on reviewed source `79e9d5284c087fbcce67eb34b3efcfe46f6fbfa6`; Academy remains on the identity-preserving `558b7ef2b5d9cf235fe9fd88abb223670ce81c19` release. Existing identities, signing keys, grants and application registrations were preserved.

That upgrade did not deploy the School permission, the optional agent-teacher switch or API workload-secret injection. The supplemental patch still targets the correct Core baseline, and the explicit three-to-four-permission installation upgrade below remains required. Permission names and ordering must match the complete reviewed application identity contract, including `academy-school` named “Manage assigned schools and their resources”. Neither a healthy canonical issuer nor source compatibility enables agent access by itself. Human School/Admin approval before publication is the confirmed policy for agent-authored student-visible content.

## Release inputs

| Input | Requirement |
| --- | --- |
| Source and CI | Final published application commit, maintained lockfiles/manifest and successful exact-commit `NCAcademy required checks`, admitted by Core's explicit repository policy. |
| API image | Private ECR ARM64 image pinned by immutable digest in `nce-deploy.json`; the same image runs migration and API. |
| Frontend | Locked `apps/web` build to `dist`, served by Amplify with same-origin `/api` proxy and no browser secrets. |
| Identity | Core organization/service/deployment UUIDs, production environment, public PKCE client and paired confidential introspection client. Register `https://ncacademy.app/api/auth/core/callback` exactly. |
| Permissions | `academy-use`, `academy-school`, `academy-teach`, `academy-audit`, plus Core's reserved human `admin` and explicit production environment access. Local school/class membership further limits every non-Admin role. Initial owner access requires both `admin` and `academy-use`. |
| Optional agent teachers | Operator-approved `allow_agent_teachers` defaults false and maps to `AllowAgentTeachers`/`ALLOW_AGENT_TEACHERS`. Enabling additionally requires the backend-only `core_workload_token` key in the already-approved runtime secret, Core account/binding/environment/resource/tag approvals and exact local placements. Migration receives no workload token. |
| DNS/TLS | Cloudflare records required by Amplify domain association and AWS certificate validation; frontend target for `ncacademy.app`, protected regional API target/certificate for `api.ncacademy.app`. Verify the emitted AWS records before changing DNS. |
| Database | Dedicated encrypted private PostgreSQL, verified TLS, separate owner/migration and runtime secrets, retained backups and restore plan. |
| Bootstrap | Stable external session/vault encryption secret and backend-only Core confidential-client injection. No secret values in manifest, browser variables, source or receipts intended for publication. |
| Audit archive | Independently retained encrypted S3 exports, KMS-signed checkpoint material, restricted operator/archive access, retention and verification/monitoring. |
| Models | Live traffic initially disabled. Enabling later requires approved endpoint/model, encrypted key, processing terms, vetted hint bank and explicit operator setting. |

No browser/search worker, vector database, content ingest or document generation service is required. Model calls are bounded synchronous selections; uncertain sends never cause automatic inference retries. Local PostgreSQL volumes must not be copied into production.

## Release order

1. Release the reviewed Core onboarding change through Core's source and deployment process. Pin its trusted runner revision and approve the NCAcademy recipe/repository configuration. Keep the exact CI gate; application manifests cannot supply arbitrary commands, templates or IAM roles.
2. Build and verify the application, publish its API image to approved immutable ECR, pin that digest in the manifest, then publish the final source commit and require its successful CI. Preserve repository history when replacing application files.
3. Provision NCAcademy's dedicated network, PostgreSQL, API and Amplify resources initially with API admission stopped. Keep the two database credentials, stable vault bootstrap and Core client credentials in the secret boundary.
4. Register the exact canonical origin, callback and permission vocabulary. For an existing Academy installation, explicitly review/upgrade its package and installed manifest from the old three-permission contract to the exact reviewed four-permission `identity` contract before release. Core deliberately rejects an implicit contract change; preserve the existing installation/service/deployment/client IDs, grants and tag policies. Add `academy-school` through the supported package/installation upgrade, without assigning it to users automatically. Keep the issuer unchanged. Transfer the paired resource-client credential directly into backend secret injection; export only public registration metadata.
5. Run `python -m academy.migrate` with the owner database credential plus `ACADEMY_RUNTIME_DB_USERNAME` and `ACADEMY_RUNTIME_DB_PASSWORD`. Migration creates schema/triggers and the restricted runtime role. The API task receives only that runtime database credential. A migration failure stops release.
6. Start the API, confirm `/api/ready`, publish the frontend with its same-origin proxy and complete Amplify domain/certificate association. Verify public DNS, TLS, canonical hostname, exact callback, Origin and CSRF behavior.
7. Grant initial access only to the owner with `admin`, `academy-use` and production environment access. The first valid login creates the app user. Verify administrative access, Core revocation and that local/demo sign-in is unavailable. Keep `ALLOW_LIVE_MODELS=false` and issue no student grants.
8. Establish an independent retained audit export and KMS-signed checkpoint through separate operator authority. Verify signature, archive retention/read access, tamper/truncation detection and synthetic restore. Complete the remaining [production acceptance](verification.md) before admitting real student records.

## Security and readiness

Production uses `AUTH_MODE=core`, `APP_URL=https://ncacademy.app`, `CORE_ISSUER=https://core.nnytech.ai`, PostgreSQL and external secrets. Core mode disables demo seeding and local role selection. Current Core authority is checked for every protected request; school membership, resource scope and class enrollment additionally limit coursework. Token expiration requires fresh Core sign-in in this initial version.

Agent support requires the supplemental [school/agent Core patch](../nce/school-agent-permissions.patch) on its reviewed baseline as well as the application image. It extends only Academy's registration and fixed recipe. The application manifest cannot enable agents, supply secret ARNs or broaden IAM. The independent agent-teacher flag conditionally injects one JSON key from the existing approved runtime secret into the API through ECS Secrets. The human Basic introspection parameter, migration credentials, API execution-role resource scope and absence of an API task role remain unchanged. Enabling agent authentication does not enable inference.

The API login receives SELECT/INSERT on `audit_events`, SELECT/INSERT/UPDATE on `audit_heads`, and the ordinary record/session DML the application needs. It has no schema CREATE, TRUNCATE, role-management or audit-event UPDATE/DELETE privileges. Migration refuses to repurpose the database owner, an elevated role or a role with unrelated ownership/membership. Migration and API use separate ECS execution roles; application code has no AWS task role. These restrictions do not prevent a database administrator from rewriting history, and the mutable audit head is not an independent anchor.

Independent audit exports and KMS signatures are operator-run. This release includes no automatic checkpoint worker or automatic retention/deletion process. Before student admission, assign checkpoint cadence and monitoring ownership, approve retention/hold/deletion rules, and verify controls against an independently trusted checkpoint. `/api/ready` checks schema/storage only; it does not certify these controls.

`ALLOW_LIVE_MODELS=false` is the initial operator setting. Enabling a connection alone cannot enable live traffic globally. Local Compose always keeps live traffic off. No deployment or health probe performs inference. Only approved public HTTPS model endpoints are supported; private-network integrations need a separate transport review.

Schema creation is explicit and versioned for the initial release. Future schema changes need a reviewed migration/rollback plan. Local Compose credentials are confined to development and do not implement production privilege separation.

## Rollback

An issuer-origin change additionally requires the [identity-preserving Core migration](core-issuer-migration.md). Apply the additive binding schema and reviewed subject backfill before changing trusted issuer configuration. Preserve both authority aliases and stable owner IDs; DNS changes alone do not migrate identity trust.

Retain the previous healthy API digest, frontend artifact, application/Core revisions, public registration binding, matching database backup and stable vault bootstrap. Stop admission and drain bounded calls before schema changes. Never replay a provider request whose send marker was committed.

Roll back only to an image compatible with the current schema and audit format. Otherwise restore the matching database into an isolated service and verify it against the independent archive head before changing traffic. A pre-release backup cannot erase later audit obligations; reconcile retained evidence before admitting users. Do not restore a version that exposes freeform model output or disables course/tenant checks.
