# API and data contracts

The machine-readable [OpenAPI reference](reference/openapi.json) captures the current typed request schemas. The API lives under `/api`; browser requests use the same origin and server-side session cookie. Mutations require both the canonical `Origin` and the per-session `X-CSRF-Token`. Error bodies omit submitted values so credential-validation failures do not echo secrets.

| Surface | Authority and purpose |
| --- | --- |
| `/auth/config`, `/auth/core`, `/auth/core/callback` | Authentication discovery and browser-bound Core PKCE sign-in. Local role selection exists only in loopback development mode. |
| `/auth/me`, `/auth/logout` | Current live-authorized identity/CSRF; local session termination remains available after Core expiration/outage. |
| `/courses`, `/courses/{id}`, `/courses/{id}/enrollments` | Course membership bounds. Instructors create/manage their courses; administrators additionally assign instructors. |
| `/courses/{id}/assignments`, `/assignments/{id}` | Instructional content and versioned tutoring policy. Student responses exclude unreleased hint/question banks and internal model binding. |
| `/assignments/{id}/draft`, `/assignments/{id}/submit` | Student-owned drafts and immutable submission snapshots. Instructors cannot edit student work. |
| `/assignments/{id}/submissions`, `/submissions/{id}/review` | Course instructor review and separately recorded feedback. |
| `/assignments/{id}/tutor` | Own approved-guidance history and idempotent bounded tutoring requests. Student input never supplies provider authority. |
| `/models` | Instructor-visible enabled model allowlist. No provider model discovery is implied. |
| `/admin/connections`, `/admin/connections/{id}` | Administrator-only named integration registry. Key inputs are write-only; status is returned, never key material. |
| `/admin/settings` | Nonsecret deployment/control status. Live model enablement is an operator deployment setting. |
| `/audit/sessions`, `/audit/sessions/{id}`, `/audit/sessions/{id}/export` | Explicit audit permission plus assigned course instructor/admin scope. Reads and exports are logged. |
| `/admin/audit/export`, `/audit/verify` | Administrator-only full tenant evidence/export and internal consistency check. Independent checkpoints remain necessary. |
| `/health`, `/ready` | Nonsecret liveness and schema/storage readiness; no inference or identity authorization cache. |

Production Core bindings validate issuer, canonical organization/service/deployment UUIDs, environment, public issuing client, service audience, bearer type, scope, expiry and subject. `academy-use` admits ordinary service access; `academy-teach` grants teaching authority and `academy-audit` permits course-scoped replay. Core's reserved `admin` grants application administration in addition to the mandatory `academy-use` permission. The first valid Core sign-in creates the local user record; Core authority is rechecked on protected requests. Role labels, email addresses and browser state confer no authority.

Storage is dedicated PostgreSQL in production. Curriculum, enrollment, work, integration and tutor-operation records retain tenant/parent/owner identity. Audit and submission immutability is also enforced with database triggers, while independently retained checkpoints protect the audit head outside the database's administrative trust boundary. The migration is explicit; readiness and callbacks do not create schema. Migration uses the database owner; the API's separate restricted login can append audit events and update the audit head, but cannot rewrite/delete/truncate events or change schema. Independent archive writes and KMS signing use operator authority outside the API.

Provider calls receive an immutable bounded prompt/policy/connection snapshot with an encrypted credential resolved server-side. The visible response and normalized usage are stored for instructor review; hidden reasoning and vendor metadata are discarded. Only an allowed instructor-authored selection is released. The protocol intentionally excludes tool use, arbitrary model output, autonomous assignment completion and automatic inference retries.
