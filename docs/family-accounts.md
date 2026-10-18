# Local students and parents

Human application Admins can create students and parents in **Students & parents**, manage profiles, reset passwords, assign schools/classes and inspect lifecycle history. Staff keep their current Core sign-in and live Admin authority. The owner's existing Core Admin account has this access; email address or being first to sign in never grants a new administrator role.

Students and parents use a NCAcademy username and password. Creation does not provision a Core, Google or other external identity. There is no public registration, email recovery, federation, account matching by email or cross-platform roster sync. A parent is optional for a student. An active parent requires at least one linked, active local student with a current school enrollment. Links are explicitly assigned by an Admin; a shared surname or email proves nothing.

## Administration and lifecycle

Create a student with a name, username and initial password; preferred name, date of birth, contact fields and administrative notes are optional. An initial school, academic year, grade and classes can be assigned in the same form. Creating an unassigned student is supported, but it does not grant coursework access. Schools and classes must belong to this deployment, and each class must belong to the selected school. Parents have their own profiles and passwords; they cannot receive Student, Teacher, School or Admin grants through enrollment forms.

Initial and reset passwords can require a change at the next sign-in. A temporary-password session can only read its own sign-in status, change that password or sign out. Admins replace passwords; they cannot retrieve the original. A self-service change requires the current password, rotates the session and CSRF token and revokes other sessions. An administrative reset invalidates existing sessions. Passwords must contain 15–128 characters, with no arbitrary composition requirement.

Enrollment records retain academic year, grade and class selections. Moving to another academic year replaces that school's current class grants while preserving earlier records; another school's independent enrollment is retained. Grade promotion is an explicit administrative action. Active, suspended, withdrawn, graduated and archived states have encrypted, immutable events with actor/time/version and administrative context. Withdrawal and other inactive states revoke sessions and current school/class access. Reactivation does not restore old class grants or sessions automatically. Past submissions and session evidence remain owned by the same stable local student ID.

When a parent's last qualifying child leaves enrollment, the parent's access is disabled and sessions are revoked. The relationship and its history remain available to Admins. A qualifying child's return can restore eligibility; the parent must sign in again. A parent's own suspension/withdrawal still prevents admission. All changes use version checks to reject stale administrative forms.

Parents currently see their own profile and linked, currently enrolled children's names, grade/year/class placement and aggregate assignment/submission/review/lesson counts. This is the initial parent feature, not a full parent portal. It exposes no drafts, conversation transcripts, administrative notes, other families or teacher permissions. Progress counts are activity indicators, not calculated grades, report cards or inferred mastery.

Local students use enrolled coursework and existing approved-source tutoring. Core resource access, Core Chat and Google Classroom linking/read APIs are denied for these accounts, and the corresponding browser entry points are hidden. This prevents those integrations from becoming indirect student-account or roster synchronization. Configured tutoring still has its existing explicit model/photo processing boundary; this feature does not claim that using an external teaching model keeps student-authored questions on the server. Voice activation remains deferred. No personal account profile, contact fields or password verifier is included in model requests.

## PII encryption

Production uses a dedicated random 256-bit PII master key, supplied to the API as `PII_ENCRYPTION_KEY` or an absolute private `PII_ENCRYPTION_KEY_FILE`. The value is exactly 32 random bytes encoded as canonical padded URL-safe base64. Supply one input only. This key is independent of the session/vault, database, Core, provider and archive keys. It is never accepted from a browser or included in API responses, Git, frontend configuration, audit events or source packages. Without it, production staff sign-in continues, but local-family operations fail closed and the password login option is unavailable. Core still controls deployment and staff authority; it does not receive family identities or this key as user data.

Profiles, normalized usernames, contact details, birth dates, notes, password verifiers, grade/year enrollment details and lifecycle snapshots are encrypted with **AES-256-GCM**. Each encryption uses a new cryptographically random 96-bit nonce and its authentication tag. HKDF-SHA256 derives a separate key for each purpose using the `NCAcademy-family-v1` salt and `encryption:` plus the purpose as context. Canonical authenticated additional data binds envelope version, tenant, owner/record ID and purpose. A changed key, tenant, owner, purpose, nonce or ciphertext cannot produce an admitted plaintext result. This follows the authenticated-encryption preference in [OWASP's cryptographic storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Cryptographic_Storage_Cheat_Sheet.html).

Passwords are never recoverably encrypted as plaintext. A random 16-byte salt and **scrypt N=131072, r=8, p=1, 32-byte output** produce the verifier; the verifier envelope is itself encrypted. Comparisons use constant-time digest comparison. These are OWASP's published minimum scrypt parameters when Argon2id is unavailable. [Password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

Username lookup uses a purpose-separated HMAC-SHA256 truncated to 192 bits over tenant and normalized username. It does not store an unkeyed dictionary hash or plaintext username. Sign-in throttles persist only keyed identifiers and counters: five attempts per normalized username and 30 per transport IP in a 15-minute window, including failed attempts. Client-provided forwarded-IP headers are not trusted. A shared proxy/NAT can share the IP allowance; production ingress and school load must be reviewed before admission. Expensive hashing admits at most two concurrent operations per process (about 256 MiB of scrypt working memory); bound worker count and verify task memory under load. Whitespace-only, single-character repetition and a small static common-password blocklist are rejected without contacting a breach service. This is an application limit, not a substitute for edge abuse controls.

New local-family educational payloads are also encrypted at the application layer: drafts, submissions, teacher reviews and pending agent review proposals for those records, assignment/lesson turns, Homework Help messages/outcomes, learning preferences/feedback and voice operation records. Their normal API projections are decrypted only inside the API process and remain subject to current own/class/reviewer authorization. School rosters use pseudonymous local labels rather than leaking the private account profile. Photos/provider secrets retain their existing inner vault protections. A secondary writer without the PII crypto context cannot persist a new local student's work or related audit plaintext.

Audit payloads involving local family actors or identified local student records are encrypted **before** the audit digest is computed. Raw complete-chain exports and the archive retain those exact ciphertext bytes so verification remains valid without the PII key. Authorized session replay separately returns `private_payload` alongside the unchanged hashed event. The archive worker receives no PII key; an archive recovery requires both its independent archive key and the PII key for private payload review. Do not replace hashed ciphertext with plaintext in a purported original audit chain.

Opaque record/tenant/student IDs, relations, role/access flags, lifecycle status, timestamps, and bounded quota/index metadata remain queryable. They are restricted operational metadata, not anonymized data. No names, usernames, contact fields, questions, answers, transcript text or student work is used as an unencrypted operational index. Request-key indexes use purpose-separated HMACs. Database and backup volume encryption and verified TLS remain additional deployment requirements. Encryption protects a database-only disclosure; an authorized running API or someone with its PII key can decrypt data. It does not protect against a compromised API or combined database/key administrator.

This is additive: historical Core/demo records and audit bytes are not silently rewritten or described as encrypted. Local demonstration mode derives a repeatable synthetic-only key from the demo session secret. It is confined to loopback, disables live providers and must never hold real children’s data. Production has no fallback to that key. No key-rotation or retention/purge job is implied: retain independent encrypted key backups, test recovery with a database snapshot, and implement reviewed re-encryption/reindexing before rotating the master key. A blind key swap makes profiles, credentials, educational content and history unreadable and changes username lookup. Keep old keys for their retained evidence lifecycle.

## Verification

From the repository root, use the locked API test environment with synthetic records only:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r apps/api/requirements-test.lock.txt
PYTHONPATH=apps/api .venv/bin/python -m pytest \
  apps/api/tests/test_local_identity.py \
  apps/api/tests/test_family_accounts.py \
  apps/api/tests/test_local_pii.py -q
cd apps/web
npm ci --no-audit --no-fund
npm test -- --run src/family-accounts.test.tsx
npm run build
```

The tests create disposable students/parents and prove authorization, school/class validation, version conflicts, password resets, mandatory password change, session revocation and parent eligibility. Raw SQL inspection checks that synthetic names, usernames, contact/support details, birth dates, passwords and student work do not appear in record or audit payloads. Cipher tests check nonce uniqueness, tamper/context rejection, independent keys and wrong-key failure; API tests check authorized plaintext replay and unchanged ciphertext audit hashes. A failing raw-store assertion is a release failure, not a reason to weaken the assertion. Tests never call real Core, Google or model providers.

For a browser rehearsal, start the local demo with `python3 scripts/setup_local.py` and `docker compose up --build -d`. Use Demo administrator → Students & parents; create a synthetic student, assign the demo school/class and require a password change. Sign out, use the username/password form, change the temporary password and save/submit a synthetic response. Return as Admin to inspect enrollment and history, create a parent linked to that student, and review the parent dashboard. Withdraw the child and confirm both its session and the parent's session are refused. Re-enroll/reactivate explicitly and confirm old class grants are not restored by sign-in. Keep all such examples fictional.

Before real admission: inject a dedicated API-only PII key through the trusted deployment runtime, run the schema-10 owner migration and matching build, verify current Core Admin access plus local login/logout/reset/disable without Core calls, and repeat synthetic raw-store/restore tests against the deployed restricted PostgreSQL identity. Confirm API/application logs and exception collection do not record request bodies or private API responses. Verify separately retained encrypted backups, key recovery, audit checkpoint/restore and current reviewer isolation. A source test pass is not evidence that production secret injection, backup encryption or retention is configured.

## API

| Route | Authority and purpose |
| --- | --- |
| `POST /api/auth/password` | Same-origin username/password sign-in; generic failure and bounded attempts. |
| `PUT /api/me/password` | Current local account password change; current-password proof and CSRF. |
| `GET /api/me/family` | Own profile; parents' eligible linked-student summaries only. |
| `GET/POST /api/admin/family-accounts` | Current human global Admin list/create. Filters: school, kind, inactive. |
| `GET/PATCH /api/admin/family-accounts/{id}` | Admin profile details/update with expected version. |
| `PUT /api/admin/family-accounts/{id}/password` | Admin replacement password and temporary-password policy. |
| `POST /api/admin/family-accounts/{id}/enrollments` | Admin school/year/grade and exact class assignments. |
| `POST /api/admin/family-accounts/{id}/lifecycle` | Admin explicit state/grade progression and immutable history. |
| `PUT /api/admin/family-accounts/{parent}/students` | Admin nonempty set of eligible local children. |
| `GET /api/admin/family-accounts/{id}/history` | Admin decrypted immutable lifecycle evidence; audited access. |
