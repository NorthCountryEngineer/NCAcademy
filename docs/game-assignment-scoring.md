# Game assignment points

An Academy class owns its point policy independently of the Game Studio game. The same published game can be a 25-point assignment in one class, a 100-point assignment in another, or unscored practice. Changing a scale does not edit or republish the reusable game.

## Set up an assignment

1. Open the class's **Practice games** workspace and prepare a published game draft. Supply the lesson label and learning objective.
2. Enable assignment points. Choose **25**, **100**, or a custom maximum, then choose how to calculate points, which attempt counts and the attempt limit. The saved structured settings can be reused on another game.
3. A human School/Admin reviews the content, every level, learning objective and point policy before approving the exact class attachment. Editing its scoring policy removes that approval and requires review again.
4. An enrolled student starts an attempt. Academy admits the current publication and creates the attempt before returning the isolated local player. The first terminal completion or loss is saved once; restarting the same frame cannot submit another result for that attempt.
5. Students can see their own attempt history and provisional points. Current authorized human course staff can review the observations and explicitly award points with a reason. Provisional results are separate from reviewed coursework credit.

Existing attachments without a scoring policy remain practice. A staff preview does not consume a student's attempt or submit credit. The fixed player has no student identity, application credentials, network access, storage or public leaderboard integration.

## Repeatable policy

The version-1 policy uses the same field names and arithmetic as Game Studio's host SDK:

```json
{
  "version": 1,
  "max_points": 25,
  "method": "proportional",
  "rounding": "hundredth",
  "attempt_policy": "best",
  "max_attempts": 10,
  "reveal_policy": "zero"
}
```

| Setting | Behavior |
| --- | --- |
| `max_points` | Whole-number assignment maximum from 1 through 10,000. |
| `method: proportional` | Objective units completed divided by the entire game's objective total, multiplied by the assignment maximum. |
| `method: completion` | Full points only for a validated full-game win; otherwise zero. |
| `method: arcade_target` | Raw arcade points divided by an explicitly chosen positive `arcade_target`, capped at full credit. Requires a game with arcade scoring enabled. |
| `rounding` | `whole` or `hundredth`, using half-up rounding. |
| `attempt_policy` | `first`, `latest`, or `best`, ordered by the server-issued attempt ordinal; equal best results select the earlier attempt. |
| `max_attempts` | From 1 through 100 starts per student and exact scoring revision. An abandoned start still consumes an attempt. |
| `reveal_policy` | `zero` by default; `earned` permits pre-reveal completion units for crossword-capable hosts. Academy's current arcade importer has no reveal control. |

For a game with 20 objectives across its full set of levels, 12 completed objectives yield **15/25** or **60/100** under proportional scoring. Completing an early level alone never changes the denominator to that level's objective count. Arcade bonuses are a separate measurement: the engine's conservative maximum validation bound is not an achievable target and is never used as a grading denominator.

Game Studio's SDK supports the arcade and word-game formats. Crossword units are solved entries, word-search units are found words, and Mad Libs units are filled prompts. Mad Libs measures participation, not grammatical or factual correctness. Academy's retained importer/player currently supports GameSpec versions 1 and 2 only; deploying the scoring contract does not enable version-3 word-game import.

## Evidence and authority

Each attempt retains the exact policy, game ID, published edition, specification hash, local package/publication, course context and runtime contract. A result retains its validated raw observation and deterministic calculation. Retrying the same result returns its existing evidence; a different result for an already completed attempt is rejected. The first accepted terminal observation is the only one admitted for that attempt.

Browser-reported completion and scores can be forged. They are activity evidence, not proof of identity, correct answers, learning or standards mastery. Server admission binds an observation to an authenticated Academy student and approved assignment; it cannot prove what happened outside the server. A human award is an explicit review decision, not an automatic consequence of an iframe message. This feature does not write the existing written-work assignment gradebook or infer academic mastery.

Attempt, result and award payloads use the independent PII key, including for Core-backed Student identities. Audit events retain encrypted student-related evidence. Opaque structural IDs remain queryable for access control. Staff access and awards are audited; students read only their own currently authorized class activity. Parents receive no new game-record access. No student data or result is sent to Game Studio, a model provider or a public board. See [encryption and recovery](family-accounts.md) and [audit retention](audit.md).

New attempts require current enrollment, current approved course/game/scoring context and unchanged upstream publication. Result submission rechecks current authority and local approval rather than trusting the browser's old admission. It uses the admitted fixed snapshot; it does not make another upstream publication request or remotely erase an already loaded game. Historical evidence remains immutable and reviewable through authorized class access. A changed policy starts a separate scoring revision; earlier evidence and awards retain their original scale.

## Migration and verification

Schema 16 adds immutable scoring evidence protections without rewriting existing student records, audit ciphertext, publications or keys. The runtime contract changes to `game-studio-data-v2` because its host-facing wrapper now reports saved observations; the fixed arcade engine is unchanged. Earlier game approvals require human review under the current runtime rather than silently inheriting the old attestation.

Use the locked project environment with `PYTHONPATH=apps/api:apps/archive` and `ALLOW_LIVE_MODELS=false`. Run the game-scoring/backend, game integration, PII and PostgreSQL privilege tests, the frontend game API/UI/player suites, the full required CI checks and frontend/container builds. Test with synthetic students and mocked public Game Studio responses:

- The same completion yields 15/25 and 60/100; multi-level partial completion uses the whole game denominator, and half-up rounding matches the host SDK.
- First/latest/best selection and attempt limits use server order; duplicate retries cannot add attempts or overwrite results.
- Other students, parents, foreign schools and AI teachers cannot read or award private coursework points. Human reviewers require current course authority.
- Raw database/audit payloads contain authenticated ciphertext rather than scores, reasons or other student content; wrong/missing keys fail closed.
- Revocation, enrollment loss, changed publication/policy/course context or stale runtime withholds admission/submission. Old evidence remains unchanged.
- Migration preserves earlier immutable kinds and audit protections; the restricted API login cannot modify or delete scoring evidence.

Production acceptance additionally requires matching API/frontend source, the immutable image, the successful schema-16 migration and authenticated UI checks. Tests and packaging alone do not establish a live release. No provider call or new external identity is required for scoring.
