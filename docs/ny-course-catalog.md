# All New York course options and custom classes

NCAcademy imports **all 2,030 entries** from NYSED's **2026–2027 Comprehensive Course Catalog**, across **25 subject areas**. This is the actual course-code catalog, separate from the earlier 67-pathway standards planning review. Physical education/gym, visual art, general/concert/marching band, orchestra, chorus, dance, theater, practical subjects, CTE, academic courses, AP/IB entries and the other published options are included. No subject is removed because its AI teaching material is incomplete or because it involves practical/performance work.

The source contains **1,605 secondary**, **416 prior-to-secondary** and **9 all-grades** entries. These are NYSED reporting levels, not application-imposed grade prerequisites. The source titles, descriptions, codes, suffixes and source indicators are preserved. Schools can give a class their own local title while keeping its official code and provenance.

NYSED describes this as a reporting catalog based on SCED; schools map their locally defined courses to state codes. Selecting an entry does not claim an accredited offering, teacher certification, awarded credit, a complete syllabus or a finished standards-aligned AI corpus. [NYSED course catalog](https://www.nysed.gov/information-reporting-services/new-york-state-comprehensive-course-catalog).

## Create any official course

1. Open **Courses → Create course → New York course catalog**.
2. Search by title, description or course code, or filter by subject area/reporting level. Gym and PE searches find Physical Education. Use **Regents exam courses only** when you want the state-assessment course choices; leave it off to see the entire catalog. Results are paginated without dropping other entries.
3. Select the course, review its suggested local title/description, choose the class teacher if needed and create it. Optional standards alignment is a separate choice.

The class can be created in a school using a state curriculum, a custom curriculum or no curriculum. It does not require an AI teacher, a ready corpus or school curriculum-plan publication. Existing role, school and class access controls still apply. Teacher agents' proposed class changes retain human publication approval.

NYSED's separate **2026–2027 Codes and Descriptions for Courses Ending in State Assessments** workbook contains **48 assessment rows**, referencing **26 distinct catalog codes**, of which **12 are Regents course codes**. The twelve include the retained Chemistry/Physics choices and their newer Physical Science equivalents. All are present in the complete catalog. This supplementary mapping drives the Regents filter; it does not label every catalog course a Regents exam course. [NYSED assessment mapping source](https://www.nysed.gov/information-reporting-services/vendor-support-student-management-systems-smss).

## Create a custom class with no state binding

Choose **Custom course**, enter your own title and description, and leave **Optional standards alignment** off. The saved class has `course_catalog: null` and `curriculum: null`. It does not inherit a state binding from its school. You can create assignments, enroll students and assign teachers normally.

Editing a course can remove its catalog selection and standards alignment explicitly. An API update omitting either field preserves it; explicit `null` clears it. These changes retain existing class identity, coursework, enrollments and audit history. A school-defined class can opt into a reviewed standards pathway later, but that is an explicit choice. Manual custom classes are available now; importing a custom curriculum document remains separate deferred work.

## Source integrity, API and verification

`GET /api/course-catalog` provides authenticated class-creation staff and authorized teaching agents with search and bounded pagination. Supported query fields are `school_id`, `state`, `academic_year`, `q`, `subject_area`, `level`, `regents_only`, `offset` and `limit` (1–250). It returns the complete filtered count, page, catalog hash, source provenance and available filters. The default is all courses, not Regents-only. Requests use packaged data and make no live NYSED/model request.

`POST /api/courses` and `PUT /api/courses/{id}` accept an optional `course_catalog` selector with exactly `state`, `academic_year`, `course_code` and `catalog_sha256`. The server resolves the official record itself. Browser-supplied titles, descriptions or provenance cannot replace official snapshot fields. A changed catalog hash returns 409; unknown/invalid selections return 422. Human approval revalidates an agent's pending selection against the current catalog. Saved provenance remains historical evidence when an unrelated class field is edited.

The package retains the original official workbooks and deterministic extracted JSON. The main workbook has **269,951 bytes**, SHA256 `ba10897d5193eca4c9740f717c5665ed7bcd6ab956aa4d14d1426b875ac2f438`. The assessment workbook has **22,322 bytes**, SHA256 `87e00f4d56af4e3d0abf2440d59640eb10fd73e8031868f6bfeb47b519f442d3`. The offline importer checks exact source bytes, every original course row and the assessment mappings; it executes no formulas/macros and fetches no URLs.

```sh
python scripts/import_ny_course_catalog.py --check
PYTHONPATH=apps/api:apps/archive ALLOW_LIVE_MODELS=false python -m pytest apps/api/tests/test_course_catalog.py apps/api/tests/test_course_catalog_creation.py -q
npm test --prefix apps/web -- --run src/course-creation.test.tsx
```

No new database schema or grant is required: the optional provenance is stored with existing course records. Release still needs a matching API/frontend and the current application migration/acceptance process. See [standards planning](ny-curriculum-2026-review.md), [school setup](school-onboarding.md) and [verification](verification.md).
