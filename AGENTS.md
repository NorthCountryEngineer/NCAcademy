# NCAcademy development contract

This is an independent education service for NCE Core. Read README.md and docs/architecture.md before changes. Keep documentation and docs/CHANGELOG.md current. User controls GitHub publication and cloud deployment; local design, source staging, container builds and mocked tests are authorized.

Students write and submit their own work. Never expose provider-generated prose to a student: tutoring may select only an instructor-approved hint/question ID. Server enforcement, current Core authority, course enrollment, tenant isolation and submission ownership are mandatory. No completion, essay-writing, arbitrary tools, code execution or student-defined system prompts.

Record the student-visible interaction, student attempt, immutable assignment/policy/integration/model snapshots, provider selection payload, normalized usage, refusals/errors/withholding and reviewer access. Persist a send marker before external I/O; ambiguous calls are never automatically retried. Commit audit before releasing tutor output. Hash chains are tamper evidence, not proof against database administrators; external retained checkpoints are a production requirement. Never collect hidden model reasoning.

No credentials, real student sessions, runtime databases or build outputs in Git or source archives. Keys are write-only API inputs, encrypted at rest, never prompt/audit/log data. Tests use isolated synthetic records and mocked provider/Core responses. Do not call live providers in tests.

Reuse Proposal Studio's generic administrative UX and Core deployment conventions only. Keep its private runtime data, environment files, company materials and business-domain implementation out of this repository. Preserve third-party notices for adapted UI components. Do not modify the active Proposal Studio or Core checkouts.
