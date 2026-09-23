# SISAL Release History

Format follows the release-notes convention: one entry per release, with New / Changed / Fixed / Database structure / Previous release sections.

## SISALv3.1 — (unreleased, migration draft)

Migrated 2026-08-20. This is a **local dev artifact**, not yet a `database_release` row itself — no `release_version`/`release_date` has been assigned, and it hasn't gone through the steward/curator review the framework calls for before something counts as `beta`. It exists to validate the v3.1 schema design against the real published SISALv3 data before anything gets formally released.

### New

- **`database_release`** — release-level provenance (generation, version, date, type, project, DOI). One row created: the `v3.0` baseline (see "Previous release" below).
- **`person`** — normalised contributor registry (name + ORCID). 155 distinct raw contact strings parsed down to 119 individuals initially; **114 after 2026-09-22 duplicate resolution** (see "Resolved" below).
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

### Resolved (2026-09-22)

- **5 near-duplicate contact-name pairs**, flagged at migration time as likely the same person with inconsistent spelling, deliberately not auto-merged then (a name-similarity heuristic alone isn't reliable enough to merge identities without confirmation). Each pair independently verified this session (affiliation, shared publications, and — where available — ORCID) before merging:

  | Kept | Merged in (deleted) | Evidence |
  |---|---|---|
  | Monika Markowska (`person_id` 104) | Monika Markhowska (106) | Only "Markowska" has any independent presence (Northumbria University, SISALv3 contributor); "Markhowska" is an unambiguous typo |
  | Ana Moreno (46) | Anna Moreno (100) | Same person, CSIC/IPE Zaragoza — both spellings resolve to the identical bio and publication list; "Ana" (single n) is her actual name |
  | Syed Masood Ahmad (25) | Syed Masood Ahmed (24) | Same ORCID (0000-0002-4090-9660) under both spellings — "Ahmad" is what the ORCID record itself uses |
  | Andrea Columbu (47) | Andrea Columbo (55) | Same person, University of Bologna, SISAL regional coordinator — both spellings share the identical publication list; "Columbu" (Sardinian-origin surname) is correct, "Columbo" a likely autocorrect-style slip |
  | Zoltán Kern (31) | Zoltan Kern (116) | Same person, HUN-REN Research Centre for Astronomy and Earth Sciences, Budapest — purely a stripped-diacritic variant |

  Mechanically: `entity_link_person` rows for each merged-away `person_id` were repointed to the kept `person_id` (14 rows repointed across the 5 pairs, no `(entity_id, person_id)` collisions), then the 5 duplicate `person` rows deleted. Verified via a full `build_db.py` rebuild: **0 foreign key violations**, `entity_link_person` row count unchanged at 1,022 (repointed, not lost). `person` now has 114 rows (was 119).

### Data corrections (2026-09-23)

- **CL26 (entity_id 212, Clamouse cave, site_id 108) — mineralogy corrected from `calcite` to `aragonite` for two depth ranges**: 48–88 cm (`sample_id` 206080–206100, 21 samples) and 322–344 cm (`sample_id` 206216–206227, 12 samples), 33 samples total. Based on the original publication (McDermott et al., 1999), Fig. 4 — consistent with the existing site note on this same entity, which already flags "relict aragonite... preserved in very short intervals... occasional spikes in d13C (3 arrows in upper part of figure 4c in McDermott et al., 1999)." Verified via a full `build_db.py` rebuild: **0 foreign key/CHECK violations** (confirms `aragonite` is a valid `mineralogy` value and nothing else broke).

### Tooling (2026-09-23)

- **`DS_scripts/entity_addition/add_entity.py`** replaces the June-era `importer.py`, which targeted an abandoned architecture (a live `sisalv4_dev.db` re-exported wholesale to CSV after every change, in a folder outside this repo). The new script reads and writes `csv/` directly, matching `add_project.py`/`backfill_from_csv.py`. All pre-flight validation logic is preserved (site/entity/dating/sample/lamina/references/notes checks, CrossRef DOI validation, dry-run-then-commit flow), adapted for three schema deltas since June: `entity.contact` → `entity_link_person` (resolved via the same person search-or-create flow as `add_project.py`), two new blank provenance columns on `entity` (`added_in_release_id`/`last_modified_release_id`, left NULL until a real release row exists), and `notes.site_id` as a primary key (new text is appended into the existing row rather than inserted as a duplicate). Adds a self-attested Auto-QC gate as the first prompt, and a per-entity "assign to a project?" step at commit time (writes `project_link_entity`). `correct.py` is retired in favor of `backfill_from_csv.py`; `HOWTO_data_steward.md` rewritten to match.
- Tested end-to-end in a scratch copy against the real QC-passed `Glass_LaVallina` workbook (entity `Glas`, site La Vallina reused as `site_id` 324, 532 sample rows, 25 dating rows, 1 reference). Full `build_db.py` rebuild came back with **0 FK/CHECK violations** after trimming a stray trailing period on 4 workbook enum cells (`Mg_Ca_downsampled`/`Ba_Ca_downsampled`/`U_Ca_downsampled`/`P_Ca_downsampled`) that Auto-QC hadn't caught — a genuine workbook data-quality issue, not a script bug (confirmed the value is verbatim from the workbook, not something the script introduced). Every inserted row matched the workbook on spot-check. The real `csv/` was untouched by this test — the actual `Glas` import into the live repo is a separate, later decision.

### Known data-quality items (flagged, not auto-resolved)

- **`person.orcid`** is NULL for all 114 people — SISAL hasn't collected ORCID historically. The original plan (cross-reference Neotoma's own ORCID records) turned out not to work: checked directly 2026-09-22 against both the Neotoma schema docs and a live API call, and Neotoma's `contacts` records carry no ORCID field either. Plan going forward: backfill `person.orcid` via online lookup (ORCID's own search/API, or cross-referencing each person's publications) — now unblocked by this duplicate resolution, since backfilling onto an unresolved duplicate would have needed redoing after the merge anyway.
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
