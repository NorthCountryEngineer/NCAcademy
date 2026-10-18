# Core issuer migration

The reviewed ARM64 API image containing this support is pinned by digest in `nce-deploy.json`. Image/source publication and exact-commit CI do not change the running service or issuer. Live adoption still requires the coordinated schema migration, reviewed backfill and configuration sequence below.

Core's subject UUID remains stable when an operator moves the same trusted Core installation to another HTTPS origin. NCAcademy resolves `(Core organization UUID, issuer, subject UUID)` through `core_identity_bindings` to the existing application `Record.id`. Fresh users keep the previous deterministic ID convention; subsequent access uses the recorded binding. Only an exact same-issuer legacy hash may be adopted automatically. Email, profile name, roles and browser input never link two identities.

The additive table has a composite tenant/issuer/subject primary key and a unique tenant/issuer/user constraint. Both reviewed origins can bind the same stable application user, while two subjects under one authority cannot share that owner. Login validates the target tenant, user kind and disabled status. Each protected session checks that its live Core subject still binds its accepted local owner. Course enrollment, submissions, tutor session IDs, event actor IDs and stored audit hashes remain unchanged. No public endpoint can edit bindings; the restricted API database role receives SELECT/INSERT only.

Before creating a completely new local user, every existing tenant user must already have a binding under the configured issuer. A configuration-only authority change therefore fails closed until the operator backfill. An exact same-issuer legacy owner can still be adopted, and an empty tenant can admit its first valid Core user. During a legacy upgrade, bind existing owners before admitting additional users.

## Prepare the schema and inventory

Use the normal owner-only `python -m academy.migrate` with the existing bootstrap and restricted-runtime credentials. It adds the table and exact grants without recreating records or changing submission/audit triggers. Start only the compatible image: readiness fails if the table or its SELECT grant is missing. Old unbound sessions require fresh sign-in; same-origin legacy sign-in adopts the existing owner record.

Changing an issuer is an explicit trust migration. Establish that both HTTPS origins belong to the same Core authority and preserve Core's subject, organization, signing keys and application registration. Obtain a complete canonical subject UUID inventory directly from that organization's trusted Core operator. Store it privately outside source as JSON with exactly these fields:

```json
{"tenant_id":"11111111-1111-4111-8111-111111111111","subjects":["44444444-4444-4444-8444-444444444444"]}
```

The inventory may include Core members who never used this app. Every existing application user and authority binding must be covered. A missing subject, foreign tenant, wrong record kind, conflicting alias or already-created duplicate user under the new issuer blocks the operation. Disabled accounts remain disabled. The operator must investigate conflicts; the command never merges owners or guesses from a display name.

This deployment uses one Core organization per database. Pending `oauth_flows` have no issuer or tenant column. The command rejects foreign-tenant record, login, binding, audit-head or audit-event data before clearing these globally unscoped flows. A database containing more than one tenant requires a separately reviewed migration.

## Review and apply

Run the default plan with the owner database credential and existing production environment. The CLI accepts PostgreSQL only, never starts the app, provisions schema or contacts Core/models. Plans use a read-only repeatable-read transaction and print only authority/owner UUID mappings, counts and a SHA256 digest, without names, tokens, session fields or coursework.

```sh
python -m academy.migrate_core_issuer \
  --old-issuer https://previous-core.example.test \
  --new-issuer https://core.example.test \
  --expected-tenant-id 11111111-1111-4111-8111-111111111111 \
  --subjects-file /PRIVATE_OPERATOR_PATH/core-subjects.json
```

Review the exact subject-to-existing-owner mapping and `plan_sha256`. Capture a database backup and retained audit checkpoint, stop new admission, drain bounded calls and stop the API. Keep admission stopped through application/Core configuration changes. Run the same command with `--apply --plan-sha256 REVIEWED_PLAN_SHA256` using the exact 64-character digest.

Apply uses READ COMMITTED, the same cross-process advisory lock as application writes, and SHARE ROW EXCLUSIVE locks on records, bindings, sessions, flows and audit tables. Authoritative snapshots follow those locks. Alias inserts, login/flow cleanup and one appended `auth.core_issuer_migrated` event share a single transaction. Any conflict, changed plan, lock timeout or audit failure rolls back everything. Neither record ownership nor existing audit/submission rows are updated or deleted. No automatic retries occur.

The appended event is the durable migration receipt. An identical replay reports `already_migrated`, creates no event, and preserves new sessions/flows. Changed subjects or owner mappings fail closed; obtain an updated inventory and investigate instead of overriding the receipt. Even an empty-tenant migration gets a receipt so a retry cannot repeatedly clear future flows.

Coordinate the Core public origin and every client's exact trusted issuer, then restart NCAcademy with the new `CORE_ISSUER`. Its application origin, callback, Core client IDs, tenant/service/deployment IDs, database and encryption bootstrap remain unchanged. Verify fresh sign-in returns the same application user ID, existing courses/work are accessible, role/permission revocation still works and the complete audit chain verifies. Record acceptance privately; source tests do not establish a deployed cutover.

## Rollback

Both aliases remain explicitly bound to the same owner. During coordinated maintenance, restore the previous trusted issuer configuration and clear application logins/flows through the owner maintenance process; new sign-in uses the retained old alias. Do not reverse or rewrite the migration event, submitted work or prior audit history. Keep the binding-aware image and additive schema: an older image that derives every user ID solely from issuer can fork identities created after the original cutover. Preserve the matching database and encryption bootstrap.
