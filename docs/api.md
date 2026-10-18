# API and data contracts

Verified Core identities resolve through an explicit tenant/issuer/subject binding to stable local owner IDs. Login and current-session checks validate the bound target's tenant, user kind, enabled state and subject. Authority migration has no public API; use the [operator backfill](core-issuer-migration.md). Runtime binding privileges permit SELECT/INSERT and forbid UPDATE/DELETE/TRUNCATE.

The machine-readable [OpenAPI reference](reference/openapi.json) captures the typed request schemas; regenerate it from the current application after endpoint/schema changes. The API lives under `/api`; browser requests use the same origin and server-side session cookie. Browser mutations require both the canonical `Origin` and the per-session `X-CSRF-Token`. Approved agent requests use their own Core Bearer identity and live backend introspection, not a human cookie or CSRF token. Mixed or malformed credentials fail closed. Error bodies omit submitted values so credential-validation failures do not echo secrets.

| Surface | Authority and purpose |
| --- | --- |
| `/auth/config`, `/auth/core`, `/auth/core/callback` | Authentication discovery and browser-bound Core PKCE sign-in. Local role selection exists only in loopback development mode. |
| `/auth/me`, `/auth/logout` | Current live-authorized identity/CSRF; local session termination remains available after Core expiration/outage. |
| `/schools`, `/schools/{id}` | Visible school scopes; Admin creates schools and authorized School/Admin maintains them. |
| `/schools/{id}/members`, `/schools/{id}/members/{principal}` | Active school memberships and scoped role management by human School/Admin. No local Admin grant. |
| `/schools/{id}/agents`, `/schools/{id}/agents/{placement}` | Human School/Admin approval and revocation of exact Core teacher-agent placements. No Core credential exchange. |
| `/schools/{id}/resources`, `/resources/{id}`, `/resources/{id}/resolve` | School resource catalog, role sharing and fresh Core authorization for assigned Core descriptors. A local descriptor never grants upstream access. |
| `/schools/{id}/changes`, `/changes/{id}/approve`, `/changes/{id}/reject` | Human School/Admin review of pending agent-authored student-facing changes; students cannot read pending content. |
| `/courses`, `/courses/{id}`, `/courses/{id}/enrollments`, `/courses/{id}/enrollments/{principal}` | School and class membership bounds. Teachers maintain their assigned classes; human School/Admin assigns or revokes teachers. DELETE revokes an enrollment; transfer the current class owner first. |
| `/courses/{id}/assignments`, `/assignments/{id}` | Instructional content and versioned tutoring policy. Student responses exclude unreleased hint/question banks and internal model binding. |
| `/assignments/{id}/draft`, `/assignments/{id}/submit` | Student-owned drafts and immutable submission snapshots. Instructors cannot edit student work. |
| `/assignments/{id}/submissions`, `/submissions/{id}/review` | Course instructor review and separately recorded feedback. |
| `/assignments/{id}/tutor` | Own approved-guidance history and idempotent bounded tutoring requests. Student input never supplies provider authority. |
| `/models?school_id=…` | School-scoped Teacher/School/Admin enabled model allowlist. No provider model discovery is implied. |
| `/admin/connections?school_id=…`, `/admin/connections/{id}` | Human School/Admin registry within the authorized school. Key inputs are write-only; status is returned, never key material. |
| `/admin/settings` | Admin-only nonsecret deployment/control status. Live model enablement is an operator deployment setting. |
| `/audit/sessions`, `/audit/sessions/{id}`, `/audit/sessions/{id}/export` | Human School/Admin scope, or human/agent Teacher with current audit grant and exact class assignment. Recorded saved-draft versions remain reviewable evidence; student write/draft access remains owner-only. Reads/exports are logged and partial. |
| `/schools/{id}/audit/export` | Human School/Admin export of that school's activity only, with original event sequence/hash and explicit partial-chain labeling. |
| `/admin/audit/export`, `/audit/verify` | Administrator-only full tenant evidence/export and internal consistency check. Independent checkpoints remain necessary. |
| `/health`, `/ready` | Nonsecret liveness and schema/storage readiness; no inference or identity authorization cache. |

Production human Core bindings validate issuer, canonical organization/service/deployment UUIDs, environment, public issuing client, service audience, bearer type, scope, expiry and subject. `academy-use` admits ordinary service access; `academy-school` permits scoped school administration, `academy-teach` teaching, and `academy-audit` Teacher replay when exact school/class membership also allows it. Human School administrators may review their own school through `academy-school`. Core's reserved `admin` grants tenant-wide application administration in addition to mandatory `academy-use`. The first valid Core sign-in creates the stable local user record but does not confer school/class access by itself. Role labels, email addresses and browser state confer no authority.

Agent claims use a separate Core contract at `/api/platform/v1/agent-credentials/introspect/`. Academy's scoped backend workload credential authenticates that request. Exact issuer/audience/organization, nonhuman subject/account, binding/consumer, environment/task, expiry and operation permissions must match an active approved local placement. Core checks registered resource policy; local school/class scope remains mandatory. See [permissions](permissions.md) for the complete role and agent contract.

Storage is dedicated PostgreSQL in production. Curriculum, enrollment, work, integration and tutor-operation records retain tenant/parent/owner identity. Audit and submission immutability is also enforced with database triggers, while independently retained checkpoints protect the audit head outside the database's administrative trust boundary. The migration is explicit; readiness and callbacks do not create schema. Migration uses the database owner; the API's separate restricted login can append audit events and update the audit head, but cannot rewrite/delete/truncate events or change schema. Independent archive writes and KMS signing use operator authority outside the API.

Provider calls receive an immutable bounded prompt/policy/connection snapshot with an encrypted credential resolved server-side. The visible response and normalized usage are stored for instructor review; hidden reasoning and vendor metadata are discarded. Only an allowed instructor-authored selection is released. The protocol intentionally excludes tool use, arbitrary model output, autonomous assignment completion and automatic inference retries.
