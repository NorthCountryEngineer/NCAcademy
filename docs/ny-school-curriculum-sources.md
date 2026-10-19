# New York school curriculum source review

This page describes the **partial Earth and Space Sciences teaching package**. The later [all-subject New York review](ny-curriculum-2026-review.md) extends planning coverage to Pre-K–12; it does not expand this package into a complete teaching library.

The school onboarding planner can select the reviewed `US-NY` / `earth_space_science` package for the **2026–2027** academic year and locally place a class in any of grades **9–12**. The package contains **14 selected alignment records and five original public teaching summaries**. It is a partial starting corpus, not a complete New York curriculum, a full Earth and Space Sciences course map or a Regents-preparation course. The environmental-science package remains independently available.

NYSED assigns curriculum design to local districts. Its high-school course map supports course alignment without prescribing an instructional sequence. School grade placement is therefore a local plan; selecting grade 9, 10, 11 or 12 does not claim that NYSED mandates that course in that year. [NYSED Science FAQ](https://www.nysed.gov/standards-instruction/science-faq), [official Earth and Space Sciences course map](https://www.nysed.gov/sites/default/files/programs/standards-instruction/ess.pdf).

The reviewed standards remain the **2016 NYSP12SLS**, with implementation beginning in 2017. The school year is an applicability selection, not a newly invented standards edition. New York’s current reference-table page specifies the **2026 Revised Edition** for classroom use beginning in **2026–27**, with its first examination use in **January 2027**; the **2024** edition applied to the August 2026 examination. The package links and hashes the current English reference-table PDF as review evidence. It does not redistribute those tables or turn them into automatically approved lesson text. [NYSED reference-table guidance](https://www.nysed.gov/state-assessment/reference-tables-earth-and-space-sciences).

## Scope and approval

The bundled teaching summaries address solar energy, plate boundaries, radiometric dating, the water cycle and moving weather systems. Their selected standards associations indicate useful reference connections rather than complete coverage of each expectation. Other selected expectations need additional suitable materials. Laboratories, required investigations, assessments, detailed astronomy, full climate evidence and reference-table instruction remain local curriculum work.

Creating a class or assigning a teacher builds the normal content-addressed corpus snapshot from this package and eligible class resources. That workflow records exact source and configuration hashes. The snapshot starts with a pending human approval; creating the package, running the school planner or assigning an agent does not itself approve it for tutoring. Existing teacher behavior, model policy, external-source review and student-output gates still apply. The source assembler is deterministic and makes no provider call or arbitrary URL fetch.

The source registry is explicit and extensible: a new state, subject or year needs another reviewed package and catalog registration. Exact state, subject, school year, grade band, version and review validity must match. Current review expires **June 30, 2027**. An unsupported state, grade or later school year fails closed. Custom-curriculum intake can later supply a separately validated adapter; this source package neither ingests custom uploads nor treats arbitrary uploaded documents as standards.

## Exact source evidence

Sources were fetched and reviewed on **October 18, 2026**, using verified TLS. Digests identify the exact downloaded bytes, including dynamic HTML; a later download can legitimately have a different digest and requires review. Only the bounded original summaries and short alignment paraphrases are shipped. Source PDFs, full articles, tables, images and third-party media are not bundled.

| Source | Bytes | SHA-256 |
| --- | ---: | --- |
| [NYSED high-school standards](https://www.nysed.gov/sites/default/files/programs/curriculum-instruction/hs-science-learning-standards.pdf) | 749,309 | `0f3d02ec4a52051ed6e92c3c7ce98991615f29d84e8fdc4b349c7fd8a9f74e55` |
| [NYSED Earth and Space Sciences map](https://www.nysed.gov/sites/default/files/programs/standards-instruction/ess.pdf) | 487,141 | `d90f5a178a93edde41c8abae625939c97332e1d5bd83a807833c08ff69ad4a62` |
| [NYSED Science FAQ](https://www.nysed.gov/standards-instruction/science-faq) | 88,475 | `13c113a4c9f079dd510be4909f0e4bb8afc32c03294e8007b67a7cea48e69433` |
| [NYSED reference-table guidance](https://www.nysed.gov/state-assessment/reference-tables-earth-and-space-sciences) | 59,458 | `903acecbe9c808aa7a8b333c611fd8112f12df57934b5f207f12493e3109a3a5` |
| [2026 Revised reference tables, English](https://www.nysed.gov/sites/default/files/programs/state-assessment/earth_space_science_ref_tables_2026.pdf) | 11,285,991 | `0ede34a27075ff4888353971e4c7da559708eaa897fadb6c0fc0d516e395507e` |
| [NASA Sun: Facts](https://science.nasa.gov/sun/facts/) | 326,256 | `dd31bbd8675c59deaa371689985765fb18c4806657333076616e0a85c94dcc8a` |
| [USGS Understanding plate motions](https://pubs.usgs.gov/gip/dynamic/understanding.html) | 24,181 | `4ff4bf3bd700d9cbac28cfd1d6e6ae0980929907cef2eb21bdcddb2277fa8953` |
| [USGS Radiometric Time Scale](https://pubs.usgs.gov/gip/geotime/radiometric.html) | 13,588 | `50e2e4610b12474c603723a3e42e52d8a83c281d72a03999de7b4306656a8162` |
| [NOAA water cycle](https://www.noaa.gov/education/resource-collections/freshwater/water-cycle) | 154,938 | `ba6abf0340474b06b1d0beaa3e881d0eece543dcbf741746077e097708a93f2f` |
| [NOAA weather systems and patterns](https://www.noaa.gov/education/resource-collections/weather-atmosphere/weather-systems-patterns) | 132,935 | `15515ff7cb0724c99c509404919ee0f39d691f6e5d64b84b022bee6ceb8d439e` |

The standards package records the printed source page and PDF page separately. For example, solar-system expectations appear on printed page 73 / PDF page 20; the NY-specific weather expectation `HS-ESS2-8` appears on printed page 79 / PDF page 26. These point to alignment evidence, not copied performance expectations or an assessment answer bank.

Local review downloads and the fetch receipt remain ignored under `build/ny-school-curriculum-sources/`; production requests do not read that directory. The checked-in package is `apps/api/academy/standards_data/ny_earth_space_science_2026.json`. The dedicated tests cover exact edition/hash evidence, independent package selection, each supported high-school grade, rejection of unsupported applicability, automatic source snapshot creation and separate human approval before retrieval. They use synthetic records and make no external requests.
