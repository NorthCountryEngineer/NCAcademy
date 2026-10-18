# Source review and publication

NCAcademy's application source lives in its own directory and is published to `NorthCountryEngineer/NCAcademy` under the owner's explicit authorization to replace the existing application contents. Inspect and retain the existing repository history and replace its tracked application tree in a reviewable commit. Keep unrelated Axolotl News, Core and Proposal Studio files out of this repository.

Review the staged source with `git diff --cached --stat` and `git diff --cached`. Runtime `.env`, PostgreSQL volumes, synthetic/student records, installed dependencies and generated archives must remain ignored. The included Core patch and baseline record identify the separately reviewed NCAcademy recipe and CI-policy change. Release it through Core's own source and deployment process; never copy private Core runtime state into the application repository.

`python3 scripts/check_package.py` validates source-package contracts. `--release` additionally refuses unresolved ECR image placeholders. The release manifest must pin the published API image by digest before final source publication and exact-commit CI admission.

```sh
python3 scripts/package_source.py
```

The deterministic archive and adjacent JSON inventory/checksum are written under ignored `build/`. It excludes Git metadata, environment secrets, databases, dependency trees and compiled artifacts. Verify extracted file checksums against the inventory before transfer. A checksum identifies transferred bytes; it is not a publisher signature or legal ownership review.

Preserve repository history when replacing application contents; a file-tree replacement does not require rewriting prior commits. The CI workflow verifies code and disposable local containers. Image publication and Core deployment are separate operator actions. Require passing CI for the exact published release commit, the approved Core repository policy and the manifest's immutable image reference before admission. Keep private deployment receipts outside the tracked source; document verified public outcomes without copying secret values or student records.
