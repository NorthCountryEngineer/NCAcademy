# School setup, curriculum and knowledge workflows

**Creating a class is independent of curriculum planning.** Courses → Create course offers the complete 2,030-entry official NYSED 2026–2027 catalog, including gym/PE, art, band, music, performance and career subjects, plus normal custom classes with no state binding. These options work in every school curriculum mode and do not require a ready AI corpus. The standards planner below remains a separate subject/grade planning workflow. See [all New York course options and custom classes](ny-course-catalog.md).

A human application Admin creates a school and continues into its setup workspace. A human School administrator can resume setup for an assigned school. The school stores its name, description, normalized logo, custom key/value metadata and curriculum choice. These are application settings; saving them creates no Core resource, upstream account, grant, credential or external agent deployment.

## School details and branding

The school form accepts a local PNG, JPEG or WebP logo, converts it into a small PNG and validates the actual PNG bytes again at the API. SVG, remote image URLs, animation and metadata do not enter the stored logo. Logo changes enter audit evidence as dimensions and a digest rather than image bytes. Custom metadata is bounded, uses unique keys and is rendered as text. School updates have optimistic versions; a stale setup screen must reload before making a conflicting change. Legacy updates that omit the new fields preserve their stored values.

## Curriculum source and grade/year courses

Choose one of three modes:

- **State standards:** choose an installed jurisdiction adapter, academic year, locally selected grades and subjects. The New York 2026–2027 scope review covers Pre-K through grade 12 across academic subjects, support pathways and school-selected electives. The two existing environmental-science and earth/space-science teaching mappings remain partial. Jurisdiction reviews and exact teaching packages are separate from the UI and can be added for other states.
- **Custom curriculum:** record its name and description now. The UI exposes the intended import path, but document upload, parsing and ingestion are deferred. This mode does not claim that a curriculum or corpus was ingested.
- **No curriculum:** finish school and teacher setup without a state curriculum or automatic course creation. Classes can be created separately.

The curriculum planning agent is a deterministic reviewed-source adapter. Its run selects current installed standards packages or framework scope reviews and assembles a draft course plan for each applicable grade, subject and academic year. It makes no LLM call, web crawl or student request. Exact packages supply mapped objectives; framework reviews supply cited planning topics with no invented standard codes. Missing, expired, unsupported and ambiguous combinations are visible gaps. A framework plan explicitly needs an exact alignment and teaching materials before an AI-teacher corpus can be approved.

Many New York high-school standards apply to a grade band or proficiency level. Selecting Grade 10 is a local scheduling decision unless the framework specifies that grade. The source review records actual framework editions separately from school-year applicability. See the [all-subject review](ny-curriculum-2026-review.md), [grade/topic coverage CSV](ny-curriculum-2026-coverage.csv) and [partial science source review](ny-school-curriculum-sources.md).

The subject picker supports searching, subject groups and selecting all available subjects. Pre-K is represented by grade `-1`, kindergarten by `0`. A configuration accepts up to 14 grades and 80 subjects; unsupported combinations produce gaps rather than invented courses. Class creation remains bounded to 100 explicitly selected courses per request, and the UI selects the next unpublished batch after a successful request. Schools can review a complete catalog without creating every elective at once.

A plan run is immutable and captures the school configuration/revision, package hashes, source references, course keys, objectives, steps and gaps. A human reviews the draft and explicitly chooses which classes to create. Publishing here means materializing selected course records; it does not publish assignments, lesson materials or AI feedback to students. The server rejects a changed school configuration or source plan and never substitutes a new package behind an old approval. Existing classes retain their recorded school-curriculum origin when the school is reconfigured; subsequent setup creates a new reviewed plan rather than rewriting coursework.

Class creation is transactional and uses durable idempotency receipts. Each created class carries its grade, subject, year and exact state curriculum and receives a content-addressed corpus snapshot. Repeating a completed request returns its receipt; changing a request under the same key conflicts. Course IDs and corpus snapshots are visible in setup. No students are enrolled automatically.

## Teaching team

School setup reuses the existing Teachers workspace and normal teacher form. Human and AI teachers have a portrait, name, title, pronouns, contact information, biography and expertise. AI teachers additionally have structured personality settings and bounded teaching instructions. Instructions are advisory configuration inside the platform's enforced policy; they cannot grant capabilities, publish content, override source approval or complete student work.

A base AI teacher can remain unassigned with a personality and instructions. One stable profile can later serve several grades and courses. A managed AI profile is application configuration, not a Core principal. A human profile can be created before the person's existing Core identity is linked. Linking identities and actual class teaching authority remain separate from profile planning. See [teacher onboarding](teachers-and-standards.md).

## Visible knowledge workflows

Assigning a teacher to classes automatically assembles or reuses each class's exact scoped corpus and records an immutable knowledge-workflow run. The setup workflow shows the teacher, selected classes, actual step outcomes, corpus IDs/hashes, source coverage and gaps. An unassigned base teacher has no course corpus; that state is explicit. The same corpus can serve multiple authorized teachers, while the stable teacher profile spans multiple course-specific corpora.

The visible result is a prepared knowledge snapshot, not automatic teaching approval. Follow the exact class link to review its documents, provenance and missing coverage, then approve the current corpus. Approve the teacher's current behavior/course/model configuration separately. External-media review, Core-bound agent authority, current enrollment and runtime model gates continue to apply. A newly created school never enables live models or bypasses publication review.

Runs and publication receipts are school-scoped, restricted to human Admin/School managers, and protected from updates/deletes by schema 7 at the ORM and database layers. Refreshing the workflow reads saved evidence; it does not rerun a provider. School/profile/course changes preserve previous run evidence and audit history.

## Operations

Run the explicit owner migration to schema 7 before starting this API revision. Readiness fails until the additive marker and immutable workflow protections are installed. It preserves previous identities, schools, course materials, corpora, sealed sessions and the audit prefix. No startup or sign-in path migrates a database.

Local verification uses synthetic records, reviewed installed packages and mocked Core/model transports. No live provider, student data or cloud configuration is needed for this workflow. Release still requires a matching immutable image, exact-source CI and normal Core acceptance; local staging is not a production release.
