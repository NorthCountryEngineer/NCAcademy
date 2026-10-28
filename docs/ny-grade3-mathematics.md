# New York Grade 3 mathematics teaching package

The installed `US-NY / mathematics / Grade 3 / 2026–2027` package maps the entire Grade 3 content section of the current NYSED Next Generation Mathematics Learning Standards, plus the eight Mathematical Practices. It supplies **44 content statements and components, eight practice records, 27 original teaching references and a nine-unit local sequence**. Parent statements and their subcomponents are both retained when the source prints both; these counts are not a claim that NYSED lists 44 independent top-level standards.

This closes the earlier gap in which Grade 3 mathematics had only three planning topics and no approvable teaching corpus. A prepared corpus now contains 52 standards/practice summaries and 27 public teaching references. **Complete standards coverage means each expectation has an aligned reference.** It does not mean a full year of lesson plans, exercises, diagnostic assessments or accommodations has already been authored. New York sets standards; schools choose their own curriculum, pacing and teaching methods. The explanations and sequence here are local NCAcademy material, not a state-authored curriculum or a certification of teacher expertise.

## Administrator workflow

1. In school setup, select **State standards → New York → 2026–2027 → Grade 3 → Mathematics**, save and run the curriculum planner. Inspect the exact mapping, sources, local sequence and review notes. Existing classes can instead be configured for this same context.
2. Create the selected class only if one does not already exist. A class created from the planner receives its own corpus snapshot; no students are enrolled and no material is approved by this operation.
3. In **Teachers → Jim → Assign classes**, select the Grade 3 mathematics class and save. The existing teacher personality and other class assignments remain attached to the same teacher. An external Core agent identity is optional for an app-managed AI teacher.
4. Open that class's **Standards & teaching corpus**, refresh, review all standards and local references, acknowledge the exact snapshot and choose **Approve teaching corpus** as a human School/Admin. If the school has an external-information audit policy, satisfy its independent current review before approving the admitted public corpus.
5. Review Jim's integrated model in **Manage models** and approve that exact policy separately. Under **Readiness → Profile review**, review and approve Jim's current profile after the class corpus is approved. Model, profile and corpus approvals are separate; a new assignment or changed class context requires profile reapproval.

No provider runs during preparation, saving or approval. The deployment live-model gate, enrollment, source admission, budgets and school policies remain authoritative. Assignments and shared lesson materials retain human publication approval. Existing students, classes, profiles, model credentials and audit evidence are not migrated or overwritten by installing this source package.

## Local scope and sequence

The sequence is suggested rather than mandatory. Multiplication/division and area begin early; fact fluency and Mathematical Practices continue throughout the year. Each unit records its exact standard codes and teaching-document IDs in the package.

| Unit | Local focus | Standards and components |
| --- | --- | --- |
| 1 | Equal groups and unknown quantities | NY-3.OA.1–4 |
| 2 | Multiplication strategies, related division, fluency and patterns | NY-3.OA.5, .6, .7a, .7b, .9 |
| 3 | Square units, rectangle area and decomposing regions | NY-3.MD.5, .5a–b, .6, .7, .7a–d |
| 4 | Place value, four-digit numbers and arithmetic | NY-3.NBT.1–3, .4a–b |
| 5 | Two-step situations and reasonable estimates | NY-3.OA.8, .8a–b |
| 6 | Fraction numbers, equivalence and equal-area shares | NY-3.NF.1, .2, .2a–b, .3, .3a–d; NY-3.G.2 |
| 7 | Minute intervals and metric measurement | NY-3.MD.1, .2a–b |
| 8 | Scaled graphs and fractional-inch measurement data | NY-3.MD.3–4 |
| 9 | Polygon classification, perimeter and area comparisons | NY-3.G.1; NY-3.MD.8a–b |

MP.1–8 accompany every unit. Grade 3 source pages are 45–54 (overview on 45); individual alignment rows cite printed/PDF pages 46–54. Mathematical Practices are on pages 7–9. Page 169 records the June 2019 update to NY-3.NBT.4b. Printed and PDF page numbers coincide in this edition.

Important New York details are retained: separate strategy fluency and fact recall; four-digit number reading/writing; fractions with denominators 2, 3, 4, 6 and 8, with denominator 1 for whole-number fraction representation; same-whole comparisons; minute intervals crossing an hour; grams/kilograms/liters; halves/quarters of an inch; scaled data; compound area with at most one missing side; and both regular-looking and unusual polygons. Geometric volume, compound measurement units and multiplicative comparison in MD.2b are outside the mapped Grade 3 scope. Post-assessment content remains included; no standards are filtered to fit a test blueprint.

## Provenance and review

Reviewed October 28, 2026 for the **2026–2027** school year; review expiry is June 30, 2027. The standard edition remains **2017 with June 2019 updates**, not an invented 2026 edition. [NYSED's current guidance](https://www.nysed.gov/standards-instruction/mathematics-guidance-resources) identifies full implementation from September 2022 and Grade 3–8 aligned assessments from spring 2023. [NYSED Mathematics](https://www.nysed.gov/standards-instruction/mathematics) describes standards as guides to locally developed instructional programs.

| Exact source retrieved | Bytes | SHA-256 |
| --- | ---: | --- |
| [Next Generation Mathematics P–12 PDF](https://www.nysed.gov/sites/default/files/programs/standards-instruction/nys-next-generation-mathematics-p-12-standards.pdf) | 3,736,579 | `1bc23fd7e8793c22ba2d398be43411f570b043e4a4da930662c22b81cd5def95` |
| [Mathematics Guidance & Resources HTML](https://www.nysed.gov/standards-instruction/mathematics-guidance-resources) | 83,954 | `9944a036c68df8865829b519284e1e7d4b3a2441c476d091ab2c4d1ccadf9d7b` |
| [Mathematics HTML](https://www.nysed.gov/standards-instruction/mathematics) | 63,674 | `2e444977c3b3382b78d73c3e0edbb3d95219783fc24f6d3f5fdb44b81e01ef45` |

Hashes identify the retrieved source bytes, not a fabricated standards identifier. HTML can change when navigation changes; a later different hash requires inspection rather than an assumption of a new standard. The complete PDF and HTML are linked rather than redistributed. Alignment summaries and teaching prose are original concise explanations. Their content hashes, source hashes and exact corpus/policy snapshots participate in the existing immutable audit and approval lifecycle.

Only Grade 3 mathematics receives this exact package. Other mathematics grades retain their separately reviewed planning frameworks and cannot inherit Grade 3 teaching approval. The two existing high-school science packages remain partial. Unsupported years, grades, states or version pins remain unavailable for exact approval.

## Testing and source refresh

Using the project's locked Python environment:

```sh
PYTHONPATH=apps/api:apps/archive .venv/bin/python -m pytest \
  apps/api/tests/test_ny_grade3_mathematics.py \
  apps/api/tests/test_curriculum_review.py \
  apps/api/tests/test_standards.py \
  apps/api/tests/test_school_curriculum.py \
  apps/api/tests/test_ny_school_curriculum_sources.py -q
npm test --prefix apps/web -- --run src/grade3-curriculum-ui.test.tsx
npm run build --prefix apps/web
```

The tests independently enumerate all 44 content/component codes and MP.1–8, verify every code has a bounded original reference and sequence placement, test exact grade/year/version selection, preserve other-grade framework behavior, and exercise actual synthetic class planning, corpus creation, human approval and stale-context rejection. No provider calls or real student data are used.

A reviewer can download the linked PDF with `curl -fL` and use `shasum -a 256` to compare its exact bytes. Inspect all Grade 3 pages and the updates table before changing codes or scope. Register a revised reviewed package rather than editing historical runtime corpora. The normal application release process ships the package; existing class administrators refresh and review the new snapshot explicitly. No database schema migration is introduced by this package.
