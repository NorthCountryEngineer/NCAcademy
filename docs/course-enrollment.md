# Student names and grade filtering in class enrollment

Open a class and choose **Enrollment**. Human class managers see student names rather than opaque account identifiers. A preferred name is shown alongside the full name when different; username, grade and academic year help distinguish students with similar names. Current class members also have readable names.

The picker starts with the grade or grades configured in the class's standards alignment. For a Grade 3 class, Grade 10 students stay out of the initial choices. Search by name or username and choose a specific grade, **All grades**, or **Grade not set** to narrow or broaden the list. A class without configured grades starts with all grades. Arbitrary course titles are not used to infer a grade.

This is a selection aid. Staff can deliberately enroll a student from another grade; enrollment does not promote that student or change their school grade/year. Already enrolled students are excluded from the choices. Only active Student school memberships are eligible; parents and inactive accounts are excluded. Current placement comes only from the selected school, with previous academic years and other schools kept separate. Common grade labels such as `3`, `Grade 3`, `3rd grade`, `Pre-K` and `Kindergarten` are recognized; a custom label remains visible under All grades, and is not treated as a missing grade.

Choices are paginated. Changing the search, grade, role or page clears the previous selection, and stale responses cannot replace newer results. Loading or failed choices cannot be submitted. Human Teacher account enrollment remains separate from assigning an AI teacher profile in **Configure course → Class teachers**.

## Privacy and authority

`POST /api/courses/{course_id}/enrollment-candidates/search` accepts only a bounded JSON body with `grade`, `search`, `offset` and `limit`. Search terms are omitted from URLs, browser history and ordinary proxy access logs. The existing same-origin session, CSRF and no-store response protections apply. Never enable request-body or private-response logging for this endpoint.

Current human Admin/School administrators and assigned human course Teachers may use this projection. The server resolves the school from the authorized class; browser input cannot choose another school's private roster. Students, parents, agents, unrelated teachers and other tenants cannot use it. Existing broad school directories and agent course responses retain their pseudonymous local-family labels.

Only the name, preferred name, username and current selected-school grade/year are decrypted for this view. Birth dates, contact details, administrative notes, parents, passwords and historical placement do not enter the response. Profiles and placements stay encrypted with the existing independent PII key. Reads append scope/count audit evidence without copying student names or private search terms. No upstream student identity, new schema, model call or key rotation is required.

## Verification

Use synthetic students across Grades 3 and 10, another school, an inactive account and a parent. Confirm that a Grade 3 class initially shows only matching active students, a name/username search narrows results, and All grades permits a deliberate other-grade selection. Check that already enrolled members are absent from candidates and that selection is cleared after filters change. Close the production form without saving during acceptance; do not enroll real students as test fixtures.

Backend tests cover scoped human authority, private-field exclusion, encrypted storage/audit sentinels, current placement versus history, grade aliases, paging, bounded requests and denial for agents, students, parents and other schools/tenants. Frontend tests cover the actual form, asynchronous request ordering, filtering, selection clearing and protected POST-body search. Exact-source CI, immutable image checks, migration/readiness and live preservation remain required for release.
