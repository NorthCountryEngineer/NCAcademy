# Game Studio fixed runtime provenance

Adapted from NorthCountryEngineer/Game_Studio at commit `f09860b9973b0359ea36684aad66618bf7a67bbf`. Original file hashes and Git blob IDs are in `provenance.json`. This is the same owner's fixed engine, not a copy of any service records or credentials.

The data-only v1/v2 spec validator and gameplay engine preserve authored text, level order, seeds and arcade flags. Academy changes only the isolated messaging envelope, exact-result shape checks and iframe policy. The authoring designer, API, accounts, score storage and leaderboards are not included. `arcade.highScores` remains inert authored data: Academy never creates a leaderboard or solicits a nickname.

`game-studio-data-v1` is the contract ID. The build digest hashes sorted `relative-path + NUL + SHA256(file) + LF` lines for the five implementation files enumerated in `provenance.json`; generated build constants are excluded from their own digest. Backend approval binds that digest as well as the imported spec. Update it deliberately with any runtime change.

Completion is advisory client gameplay data, never an authoritative grade. This MVP makes no progress writes.
