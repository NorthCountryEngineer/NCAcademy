# Google Classroom integration contract

Reviewed against Google's official documentation on October 18, 2026. No Google account was connected and no live student records were requested during implementation or tests.

## Supported workspace

NCAcademy can display a consenting student's enrolled Classroom courses and published assignment descriptions through the read-only Classroom API. The student links one of these courses to an already authorized NCAcademy class and keeps its tutoring workspace available. A native Classroom link opens the actual Google assignment, including Google's own editing, attachment and turn-in workflow. The NCAcademy view does not impersonate that workflow or submit student work. Google documents `alternateLink` as the native web UI address on both [courses](https://developers.google.com/workspace/classroom/reference/rest/v1/courses) and [coursework](https://developers.google.com/workspace/classroom/reference/rest/v1/courses.courseWork).

The baseline uses an assignment-and-help workspace plus a separate native Classroom window or tab. It does not attempt to reverse-proxy Google, collect Google passwords, bypass browser framing controls or inject scripts into Google pages. Google's documented [integration paths](https://developers.google.com/workspace/classroom/guides/integration-paths/integration-paths) and [add-on iframes](https://developers.google.com/workspace/classroom/add-ons/developer-guides/iframes) provide no supported general-purpose iframe for placing the entire native Classroom application inside an arbitrary website. This is a support-boundary conclusion from those contracts; no promise about future framing behavior is made.

A future Classroom add-on could put NCAcademy's own student activity inside Classroom. That is the opposite embedding direction. It requires Google sign-in, Marketplace registration/review and the required attachment-discovery, teacher and student views. Activity attachments also need a student-work-review view. Google's [developer journey](https://developers.google.com/workspace/classroom/add-ons/get-started/developer-journey) describes those prerequisites. Teachers require a Teaching & Learning or Education Plus license according to the [add-on overview](https://developers.google.com/workspace/classroom/add-ons). This baseline is not a published or licensed Classroom add-on.

## Identity and permission

NCE Core remains the NCAcademy login and authority. Google OAuth connects an additional, student-owned account to the current application user. It never grants a local school/class role. Account association uses the immutable Google subject returned by the fixed [UserInfo endpoint](https://developers.google.com/identity/openid-connect/reference), not matching names or email addresses.

The requested scopes are fixed:

- `openid`
- `https://www.googleapis.com/auth/classroom.courses.readonly`
- `https://www.googleapis.com/auth/classroom.coursework.me.readonly`

No email, profile, roster, Drive, grade/submission write, course administration or domain-wide delegation scopes are requested. See the [Classroom authorization guide](https://developers.google.com/workspace/classroom/guides/auth).

Course enumeration always uses `studentId=me`, `courseStates=ACTIVE`, and a bounded page size. A current exact match within the bounded course search must establish membership before mapped coursework is read. An absent match or failed lookup is not permission. The API's [course listing contract](https://developers.google.com/workspace/classroom/reference/rest/v1/courses/list) specifies this student filter and requires otherwise identical requests when following page tokens. No additional roster scope is needed for this membership check.

Coursework reads request only `PUBLISHED` items for that exact verified course, 50 per page. Students can see only published work according to [courseWork.list](https://developers.google.com/workspace/classroom/reference/rest/v1/courses.courseWork/list). The adapter projects assignment IDs, course IDs, title/description, due date/time, work type and an admitted native link. It discards attachment/Drive data, question choices, grades, submission data and unknown response fields. Local enrollment, current account, connection revision and operation admission remain separate server checks.

## OAuth and transport

The backend constructs Google's fixed authorization URL and exchanges a code at the fixed token endpoint. Authorization uses a random, expiring, one-use browser-bound state, S256 PKCE, a fixed same-application callback and the confidential client's server-held secret. Tokens remain encrypted server-side. The browser receives no access token, refresh token, verifier, client secret or unverified ID token. Backend orchestration persists admission markers before token exchange, refresh and revocation and treats an ambiguous result as requiring explicit recovery/reconnection. The adapter never automatically retries.

Google documents confidential code exchange, offline access, refresh and revocation in its [web-server OAuth guide](https://developers.google.com/identity/protocols/oauth2/web-server). Its [OAuth reference](https://developers.google.com/identity/openid-connect/reference) describes PKCE discovery and its [web authorization guidance](https://developers.google.com/identity/protocols/oauth2/javascript-implicit-flow) recommends authorization code plus PKCE instead of the implicit flow. The implemented flow uses a normal full browser, with encrypted token storage and state validation consistent with Google's [best practices](https://developers.google.com/identity/protocols/oauth2/resources/best-practices).

`GoogleClassroomTransport` refuses real calls unless `AUTH_MODE=core` and the independent integration flag is enabled with valid server configuration. Tests inject a mock HTTP client. Production endpoints are fixed to Google's authorization, token, UserInfo, revocation and Classroom API hosts. There are no caller-provided hosts, attachment downloads, proxy lookups, cookies, forwarded Core credentials, redirects or automatic HTTP retries. Responses have byte/time/page caps, duplicate-key rejection, exact grant checks and validated strings/IDs/dates. Failures expose static metadata, never Google's error description, body or credential-bearing HTTP exception.

Only exact `https://classroom.google.com` native links with no credentials, ports, query, fragment, control characters or encoded path separators survive projection. Malformed links are omitted. Text is displayed as inert text by the application. Cursor values remain opaque and bounded, never authentication or authorization evidence.

## Google data use

Imported Classroom content is a transient owner-only display. It is not a public teaching corpus, administrator-readable session transcript, model input or instruction. The help handoff carries the already-authorized local course, leaving the student to describe their question. There is no automatic insertion of Google assignment descriptions, titles, Google identifiers or attachments into an LLM prompt. Durable integration evidence records sanitized operations/outcomes rather than private assignment text or credentials.

Google's [Workspace user-data policy](https://developers.google.com/workspace/workspace-api-user-data-developer-policy) limits data use to visible, appropriate features, restricts transfers and human reading, requires honoring deletion requests, and restricts training or improving general AI models with Workspace data. Introducing model transfer, human review or persistent imported-content storage would require a separate disclosed, consented and policy-reviewed feature. The current integration must not be described as automatic AI ingestion of Classroom coursework.

Disconnect erases locally retained connection credentials and private mappings. Programmatic revocation is separate from local erasure, and an uncertain remote outcome must remain visible. Google notes that revocation invalidates the user's previously granted scopes across clients in the same Google Cloud project; a dedicated NCAcademy Classroom OAuth project avoids surprising effects on unrelated integrations.

## Operator prerequisites

Production activation requires a Google Cloud project with the Classroom API enabled, a web OAuth client, the exact callback registration, verified owned domains/branding and publicly accessible privacy/terms information. Applicable sensitive-scope verification must be completed. Google documents these requirements and the use of full browsers in its [OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies). A secret/configuration placeholder is not a verified OAuth application.

Workspace school administrators can restrict app access, and must configure third-party applications for users under 18; see [Classroom administrator actions](https://developers.google.com/workspace/classroom/guides/key-concepts/admin-actions). Student consent cannot bypass that institutional restriction. Account and license eligibility need live acceptance with an authorized synthetic school domain before real student admission.

The baseline uses explicit refresh and small server budgets rather than polling Google continuously. Actual project quotas remain operator-owned; see Google's current [usage limits](https://developers.google.com/workspace/classroom/reference/limits). No live availability, consent-screen approval, administrator allowlisting or Google account linking is established by the local mocked tests.
