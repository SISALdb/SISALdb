# SISAL Release History

Format follows the release-notes convention: one entry per release, with New / Changed / Fixed / Database structure / Previous release sections.

## SISALv3.1 — (unreleased, migration draft)

Migrated 2026-08-20. This is a **local dev artifact**, not yet a `database_release` row itself — no `release_version`/`release_date` has been assigned, and it hasn't gone through the steward/curator review the framework calls for before something counts as `beta`. It exists to validate the v3.1 schema design against the real published SISALv3 data before anything gets formally released.

### New

- **`database_release`** — release-level provenance (generation, version, date, type, project, DOI). One row created: the `v3.0` baseline (see "Previous release" below).
- **`person`** — normalised contributor registry (name + ORCID). 119 distinct individuals, parsed from the 902 `entity.contact` values across the published dataset.
- **`entity_person`** — junction table linking entities to their contact person(s). Replaces the old free-text `entity.contact` column. 1,022 link rows (some entities have up to 3 contacts, e.g. entity 451 "MAW-0201": Nikita Kaushal, Jessica Oster, Sebastian Breitenbach).
- **`release_person`** — role-tagged link between a release and the people responsible for it (`release_steward` / `data_curator` / `workflow_developer` / `project_lead` / `data_contributor`). Empty for now — `data_contributor` is derived from `entity_person` + `entity.added_in_release_id` rather than stored, and the other four roles need a human to assign them (nobody currently gets credited as v3.0's steward/curator/etc., since that predates this framework).
- **`code_artifact`** — DOI-citable code versions for the age-model and downsampling pipelines. Empty — no DOIs assigned yet.
- **`entity.added_in_release_id`** — which release first introduced the entity. Immutable. Backfilled to the `v3.0` row for all 902 entities.
- **`entity.last_modified_release_id`** — which release most recently touched the entity. Mutable. Also backfilled to `v3.0` for all 902 entities (nothing has been modified since).

### Changed

- **Real types, primary keys, foreign keys, and `CHECK` constraints across all 21 published tables.** The two existing local SQLite copies (`sisalv3.db`, `sisalv4_dev.db`) had lost all of this during their original CSV import — every column was bare `TEXT`, no relationships were enforced, and all ~30 controlled vocabularies were unrestricted free text. This migration rebuilds the schema from the real MySQL DDL (`sisalv3.sql`) with the mapping documented in `sisalv3_schema.dbml`: `int unsigned AUTO_INCREMENT` → `INTEGER PRIMARY KEY`, `double`/`decimal` → `REAL`, `enum(...)` → `TEXT CHECK (... IN (...))`, `FOREIGN KEY ... ON DELETE CASCADE` → carried over as-is.
- **`entity.contact`** (free text, sometimes multiple names comma/semicolon/slash-separated) → `entity_person` (structured, one row per person). 155 distinct raw strings resolved to 119 distinct individuals after splitting and deduplication.

### Fixed

- Foreign key integrity was previously **unverifiable** in both existing local copies (no constraints existed to check against). This migration loads all data with `PRAGMA foreign_keys = ON` — any row violating a relationship would have failed the load. Result: **0 foreign key violations** across the full published dataset.

### Known data-quality items (flagged, not auto-resolved)

- **5 near-duplicate contact-name pairs**, likely the same person with inconsistent spelling across entries — deliberately *not* auto-merged (a name-similarity heuristic isn't reliable enough to safely merge two people's identities without confirmation):

  | Similarity | Name A | Name B |
  |---|---|---|
  | 0.97 | Monika Markowska | Monika Markhowska |
  | 0.952 | Ana Moreno | Anna Moreno |
  | 0.941 | Syed Masood Ahmed | Syed Masood Ahmad |
  | 0.929 | Andrea Columbu | Andrea Columbo |
  | 0.909 | Zoltán Kern | Zoltan Kern |

  Needs a manual decision per pair: same person (merge the `person` rows and repoint `entity_person`) or genuinely different people (leave as-is).
- **`person.orcid`** is NULL for all 119 people — SISAL hasn't collected ORCID historically. Neotoma already has ORCID for these same contributors; cross-referencing that in is the next step, along with an open `person.neotoma_contributor_id` question still to be resolved with the Neotoma team.
- **`database_release.release_date` and `.release_doi`** are NULL on the `v3.0` row — the exact original publication date wasn't found in local files, only the DOI reference (`10.5287/ora-2nanwp4rk`, per `sisalv3_db_reference.md`). Worth filling in from the actual SISALv3 publication record.
- **`PRAGMA foreign_keys = ON` is per-connection, not stored in the file** — any tool/script opening `sisalv3.1.db` needs to set this itself, or constraints silently stop being enforced (this was the exact failure mode that caused the original metadata loss).

### Database structure

- 21 published SISALv3 tables (unchanged column meaning, corrected types/constraints)
- 6 new draft v3.1 tables: `database_release`, `person`, `entity_person`, `release_person`, `code_artifact`
- 2 new columns on `entity`: `added_in_release_id`, `last_modified_release_id`

### Verification

- Row counts match the published CSVs exactly for all 21 tables (902 entities, 448,573 samples, 319,684 sisal_chronology rows, etc. — full counts in `migration_log.txt`).
- Spot-checked entities 202 (K11, Korallgrottan cave), 33 (Vil-stm1, Villars cave), 212 (CL26, Clamouse cave) against `sisalv3_db_reference.md` — coordinates and names match exactly.
- 0 foreign key violations (`PRAGMA foreign_key_check`).

### Previous release

- None — `v3.0` is the baseline backfill row for the already-published SISALv3, created retroactively during this migration so `previous_release` chains have something to point to going forward.

---

## Files in this repo

| File | Contents |
|---|---|
| `csv/` | The full SISALv3.1 data, one CSV per table — diff-friendly source of truth |
| `schema/schema.dbml` | Full schema (21 published tables + draft v3.1 additions), pasteable into [dbdiagram.io](https://dbdiagram.io) |
| `scripts/build_db.py` | Builds `sisalv3.1.db` from `csv/` |
| `migration_log.txt` | Row-count/verification output from the original v3 → v3.1 migration |
| `CHANGELOG.md` | This file |

`sisalv3.1.db` itself (~114 MB) is **not** committed to this repo — see the README for why and how to build it.
