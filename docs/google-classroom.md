# Google Classroom companion

Platform-local student and parent accounts cannot use this integration. Their Google linking, reads and callback admission are denied in the API and hidden in the browser. The companion below applies only to separately Core-authenticated students; it does not create or synchronize local minors' external accounts. See [local family boundaries](family-accounts.md).

Students can connect their own Google account, link an enrolled Google Classroom course to an enrolled NCAcademy class, and read published assignments with NCAcademy help alongside them. The native assignment opens in a separate Google Classroom tab for working on documents, attaching files and submitting work. NCAcademy retains the class context and its private Homework Help conversation.

Google's supported embedding model puts an approved add-on inside Classroom. It does not supply a general-purpose iframe for putting the whole Classroom website inside NCAcademy. This implementation uses the supported read-only API and native assignment links. An in-Classroom NCAcademy add-on would require a separate Marketplace application, review, views and eligible school licenses; it is not part of this package. See the [official-source contract](google-classroom-contract.md) for the support boundary and evidence.

## Student workflow

1. Open an enrolled NCAcademy class and select Google Classroom.
2. Select Connect Google Classroom and complete Google's sign-in/consent in the normal browser. NCE Core remains the NCAcademy login; Google consent only connects an additional personal account.
3. Explicitly load your Google classes and link the matching one. Only your active student courses are offered. Each student maintains their own mapping; another student's mapping or credentials are never reused.
4. Load published assignments, choose one and read its details. Use Open in Google Classroom for Google's native work and turn-in flow.
5. Open NCAcademy help alongside the assignment. Describe the difficulty in your own words and choose an approved course teacher. The local class can be preselected; Google assignment text, titles and identifiers are not inserted into the question.

The help panel uses the existing learning intake, approved teacher/corpus/model policy, private session audit, quotas, voice options and instructional limits. It guides the student's own work. This integration does not grade, write, attach, submit or turn in Google work. It does not download Drive files, read submission answers or import assignments into the teaching corpus.

Staff and teaching agents do not receive a student's Google account access. The class tab explains the student-owned connection rather than exposing private coursework to a teacher or administrator.

## Private data and current access

The three fixed scopes are `openid`, `classroom.courses.readonly` and `classroom.coursework.me.readonly` under Google's standard authorization namespace. There is no roster, email/profile, Drive, administrative or write scope. The fixed UserInfo endpoint supplies the stable Google subject; email/name matching never creates a local identity or role.

Google credentials, subject and course mapping IDs are encrypted at rest using the existing stable server vault bootstrap. The browser receives no OAuth token, verifier or client secret. Assignment content is transient owner-only display, with no browser persistence, automatic model transfer, administrator-readable replay or archive copy. Durable integration evidence contains local course/school/operation IDs, counts and sanitized outcomes. Existing Homework Help keeps the student's own question and released guidance under its usual school review rules.

Each explicit read checks the current Core login, human Student role, school membership, local enrollment and exact connection/link/configuration revision before and after external I/O. Mapped assignment reads also prove current Google student enrollment using the active-course list. Account changes, unlinking, disconnects and revoked enrollment withhold in-flight results. Changing the Google client or callback requires reconnection; rotating the same client's secret does not change its identity. Imported text is rendered as plain text; native links are restricted to Google Classroom's exact HTTPS host. No iframe, external preview, attachment download or arbitrary URL fetch is used.

OAuth uses expiring browser/login-bound one-use state, PKCE and the fixed callback. A durable marker commits before exchange, refresh, reads or revocation. An ambiguous exchange or refresh is never automatically replayed; reconnect explicitly. A page read can be explicitly requested again with current authorization. The 300-operation daily integration allowance is separate from tutoring limits; owner cleanup is never blocked by an exhausted read allowance. Coursework pages contain up to 50 assignments; Google class pages contain up to 100 courses. Membership verification searches at most ten pages and fails closed if it cannot establish the exact course within that bound.

## Disconnect and recovery

Unlink removes this local class's mapping. Disconnect clears the student's local account credentials and mappings for that school, then attempts Google revocation. Local erasure does not depend on a successful Google response. The remote result remains visibly unknown if revocation was interrupted; repeating disconnect does not claim success or resend the removed credential. Google revocation can affect other OAuth clients/scopes in the same Google Cloud project, so use a dedicated Classroom project.

Owner-only connection-management endpoints permit cleanup even after a class or school membership is removed. Google also exposes account permission removal through its own account settings. Neither another student's identifier nor a School/Admin role authorizes clearing or reading someone else's connection.

Schema 9 adds protected local link events and sealed operation receipts. The owner-only additive migration preserves previous identities, coursework, chat records and audit hashes. There is no permanent Google-content snapshot in those receipts. Keep the stable vault bootstrap through upgrades and database restore; rotating it without migration would make existing credentials unreadable.

## Production configuration

The feature defaults off and local demonstration authentication refuses live Google calls. Tests and the review lab use explicit synthetic transports; no Google account is connected by source preparation.

An operator must register a dedicated Google web OAuth client with the Classroom API enabled and the exact callback `https://ncacademy.app/api/google-classroom/callback`. Google's applicable branding/domain, privacy/terms and scope verification must be complete. School Workspace policy may require administrator approval, including the under-18 application allowlist. Those Google-side requirements are independent of NCAcademy enrollment.

The backend inputs are:

| Input | Purpose |
| --- | --- |
| `GOOGLE_CLASSROOM_ENABLED` | Independent strict true/false operator gate; default false. |
| `GOOGLE_CLASSROOM_CLIENT_ID` | Dedicated Google web OAuth client ID. |
| `GOOGLE_CLASSROOM_CLIENT_SECRET` or `GOOGLE_CLASSROOM_CLIENT_SECRET_FILE` | One private server-held secret input. An absolute bounded UTF-8 file may be supplied instead of the value. |

The existing Core deployment recipe does not yet expose independent Google Classroom configuration. Activation requires a reviewed trusted recipe/runtime injection for these API-only values. Application manifests cannot enable the feature or carry secrets; the frontend and migration task need no Google credential. Keep models, Core Chat and agent-admission gates independent. This package changes no cloud project, consent screen, Core recipe or running service.

Release requires the matching immutable API image, schema-9 migration, frontend artifact and normal exact-source checks. Verify consent, denied/partial scope behavior, school account eligibility, native assignment links, refresh/revocation, enrollment changes and private-data retention with an authorized synthetic Google school account before student admission. A mocked local preview does not establish live Google acceptance.
