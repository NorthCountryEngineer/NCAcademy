# Administrative settings and agent activity

NCAcademy follows Proposal Studio's administration layout while keeping school policies, credentials, records and execution separate. Open **Administrative settings** for the currently selected school. The application Admin navigation remains distinct from school membership and from an AI teacher's profile.

## Integrations

Browse the searchable integration catalog, filter by category and open a card to configure a supported service. **Configured integrations** shows saved connections and their current status. Creating a connection or editing its settings does not start an agent or send a provider request.

Model connections use the existing compatible-endpoint adapter and an explicit list of allowed model IDs. Blank replacement keys preserve a saved key; an explicit clear removes it. Saved keys are encrypted and never returned by status APIs. Saving an enabled connection does not enable the deployment's live-model gate or approve a teacher, corpus or Homework Help policy.

Voice, Google Classroom, Core chat and external-media cards lead to their existing settings or explain the operator configuration they require. A card's presence means the application has a supported integration path; it does not claim that an external service, worker or account is connected. This catalog does not install connector code or accept arbitrary HTTP tools.

## Administrative agents

Choose **Create administrative agent** and enter a name, description, task instructions and optional school reference context. Select registered skills and the execution mode. These are application job profiles: creating one does not create a Core principal, assign a Teacher role or grant access to student records.

**Local review** runs bounded application checks without a model. Use it to inspect school teaching readiness or prepare a draft plan from the installed reviewed curriculum sources. Its registered checks are fixed; instructions and reference context guide the optional model review rather than changing these checks. Curriculum plans remain drafts; the normal school setup flow creates selected classes through an explicit human action. Readiness findings are advisory and cannot approve teaching materials or weaken a policy.

**Model review** adds a bounded advisory review through a selected school model connection. Configure the model, input/output limits, verified token prices and per-run budget. The deployment live-model gate, current school authority, enabled connection and allowed model ID must all permit execution. Missing pricing or insufficient budget blocks a model run before sending. Local review remains available without model credentials.

Costs are estimates at the administrator's configured rates, not verified provider invoices. Run details distinguish a reserved maximum, whether provider invocation was attempted and validated reported usage. A known failure before invocation records no provider call; an uncertain invocation retains its reservation and is not resent.

Saving an agent starts no work. Start a run explicitly from its card. Each run retains the accepted agent and skill versions, administrative context, execution steps, selected model and available usage/cost evidence. Editing, disabling or archiving a definition preserves its history and accepted runs. Version conflicts require reload rather than silently overwriting another administrator's changes.

Administrative context excludes student conversations, submitted work, photographs and private family details. Do not paste credentials or student information into instructions or reference context. Model recommendations remain untrusted draft text for human review; the application executes only its registered actions.

## Skills

The skills catalog lists fixed application actions and administrator-defined skills built on those actions. A custom skill supplies reusable metadata and instructions; it cannot invent a new effect, grant authority, execute code, browse arbitrary URLs or change the publication rules. Inputs come from the current authorized school's bounded configuration and aggregate readiness, rather than arbitrary caller-supplied data or student records.

Edit, enable/disable, archive/restore and version history keep stable skill IDs and retain earlier definitions. Archived or disabled skills cannot be selected for a new run. Existing run evidence keeps the exact accepted versions. Built-in actions are reference definitions and cannot be replaced through custom-skill APIs.

## Agent activity

Open **Agent activity** in the administrator sidebar or its tab in Administrative settings. The selected school determines the scope. Filter the recorded activity, refresh deliberately and open an administrative run for its steps and retained result. The view combines administrative runs with the application's existing curriculum, knowledge-preparation and other agent activity.

Activity cards identify the actual execution mode: a local source adapter is not labeled as a model invocation. Model details distinguish available usage and configured-price estimates from unknown cost. Failed, blocked, withheld and uncertain work stays visible. An uncertain provider send is never automatically retried; refreshing history reads the saved outcome. If a request was rejected before acceptance, the explicit recovery form can submit its retained request key against the reviewed current configuration. It warns that an unaccepted request may start at that point. The server returns an existing matching run or rejects a changed request before execution; recovery never silently creates a new request key.

This activity view is operational metadata, not student-session replay. It excludes student names, conversation text, assignment photographs, provider credentials and raw provider responses. Use the separate **Student sessions** or **Audit** workspace for authorized encrypted session review. The new views do not change who can read those records.

## Policies and testing

**Tutoring policy**, **Homework Help AI**, **Audit** and **Application configuration** remain available. Existing teacher/corpus/model reviews, student authorship, independent Homework Help safety review and external-media publication policy continue to govern their respective features. Administrative agents cannot approve their own output, modify policy or bypass these controls.

Definitions and activity are stored in NCAcademy's own database. Revision and accepted-run evidence receive database-level immutability protections as well as API checks and audit events. Schema 13 preserves the earlier schema-2–12 evidence and adds the administrative-workspace protections.

Run the focused tests with the locked dependencies described in [the README](../README.md):

```sh
PYTHONPATH=apps/api:apps/archive .venv/bin/python -m pytest apps/api/tests/test_administration.py apps/api/tests/test_database_roles.py -q
npm test --prefix apps/web -- --run src/administration-api.test.ts src/administration-ui.test.tsx
```

The PostgreSQL cases require `ACADEMY_TEST_POSTGRES_URL` to point to an isolated disposable test database. Exact-commit CI provisions that fixture and exercises restricted-runtime CRUD, JSONB activity pagination and SQL rejection of revision/result mutation. Local tests with no fixture report those cases as skipped. Model transports are mocked; tests use no real credentials, student records or paid provider calls.

For a browser rehearsal, use an isolated local demo. Create a readiness skill and local-review agent, run it once, reopen the activity result, then edit a definition and inspect its retained versions. Verify archive/restore and stale-version errors. Confirm that student/teacher accounts cannot open administrative APIs, that an unavailable model cannot execute, and that refreshing an ambiguous launch reads history without a new send. Do not use production teacher approval or student content as test data.

## Deployment

Deploy through the normal Core exact-commit process. Preserve the independent PII key, historical ciphertext/audit chain, existing identity bindings and runtime gates. After upgrading, do not run an older owner migration that removes schema-13 immutable kinds from the SQL protections. Keep those protections when preparing a rollback. See [deployment](deployment.md), [operations](operations.md) and [verification](verification.md).
