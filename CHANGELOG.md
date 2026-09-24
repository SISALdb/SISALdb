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
- **`add_entity.py` updated** to catch that exact class of problem before `--commit` instead of only at the `build_db.py` rebuild: it now parses `schema.dbml` (same schema-driven pattern as `backfill_from_csv.py`) and validates every enum-typed column on the Site/Entity metadata sheets against the real controlled vocabulary. A near-miss (differs only by trailing punctuation/whitespace/case — exactly the `Mg_Ca_downsampled` etc. case above) names the exact valid value to use; any other mismatch lists the full allowed set. Both are pre-flight errors and block `--commit`. Re-verified against the real `Glass_LaVallina` workbook: dry-run now reports the 4 trailing-period mismatches as errors and `--commit` correctly refuses to write until they're fixed.

### New entities (2026-09-23)

- **Glas (entity_id 903, La Vallina, site_id 324)** — real import via `add_entity.py`, the first production run of the new tool. Source: `QC_passed_SISAL_workbook_v15_Glass_LaVallina_UThchecked.xlsx`. Site La Vallina was reused (already in SISAL from prior entities Gael_2022/Gael_2015/Gloria/Garth/Gulda/Luna/Galia). 532 sample rows, 25 dating rows, d18O/d13C/Sr_Ca/Mg_Ca proxies, 1 new reference (`10.5194/egusphere-2025-3911`), contact Laura Endres linked via `entity_link_person`, not assigned to a project. Site notes text appended to the existing `site_id=324` notes row rather than duplicated. Fixed the 4 trailing-period enum cells (`Mg_Ca_downsampled`/`Ba_Ca_downsampled`/`U_Ca_downsampled`/`P_Ca_downsampled`, cells AK3/AP3/AU3/AZ3 on the Entity metadata sheet) directly in the source workbook before import, per the new pre-flight enum check above. Verified via a full `build_db.py` rebuild: **0 FK/CHECK violations**; every inserted row spot-checked against the workbook.
- **`entity.iso_std` corrected** `PDB` → `Vienna-PDB` (the workbook always writes the short form; same manual fix flagged for prior imports in the old HOWTO). Applied by hand directly in `csv/entity.csv`, since `add_entity.py`/`backfill_from_csv.py` don't currently cover a single non-`sample_id`-keyed entity field correction (see `HOWTO_data_steward.md`, Workflow A). Verified via `build_db.py`: 0 FK/CHECK violations.

### Tooling (2026-09-24)

- **`DS_scripts/release_finalization/get_wokam.py`** added — first script toward the release finalization pipeline (`wokam`/`copernicus_lcc` backfill, `SISAL.AM` age models, GitHub Actions auto-push; see the "Release finalization pipeline" note in the SISAL-Neotoma vault). Backfills blank `entity.wokam` values via point-in-polygon lookup against the WOKAM (World Karst Aquifer Map, BGR/WHYMAP) shapefile, keyed on each entity's `site.latitude`/`longitude` — deterministic, no live API dependency, same pattern as `build_db.py`. Follows the `add_entity.py` `--dry-run`/`--commit` convention: dry-run previews the resolved value breakdown and lists any site with no polygon match or any entity already set (left alone, never overwritten) before anything is written. Schema-driven where it matters: validates its raw-shapefile-value-to-enum mapping against the real `wokam_enum` in `schema/schema.dbml` at startup, and refuses to guess (exits with the actual columns/values found) if the downloaded shapefile's attribute name or values don't match what the script expects.
- New `DS_scripts/release_finalization/data/` folder (git-ignored except for its `README.md`) holds the local WOKAM shapefile — too large to commit and not ours to redistribute, so each steward downloads their own copy per the README's link and drops it in unzipped. New third-party dependencies for this one script: `geopandas`, `shapely` (not yet in a repo-wide requirements file — install directly, per the script's own docstring).
- **Corrected against the real download**: the assumed `ROCK_TYPE` attribute name was wrong — the actual karst rock-type field is `RTypeLabel`, on the `whymap_karst__v1_poly.shp` layer specifically (the WHYMAP_WOKAM_v1 package ships several other `.shp` point layers — cave, spring, non-exposed-karst — alongside it). `find_shapefile()` now picks the layer that actually has the expected field instead of guessing by filename/position, so a future re-download with a different file name still works.
- Also corrected: `entity.wokam` is **not** blank for every entity as originally assumed in the vault planning note — 654/903 already carry a value, since it's an original published SISALv3 field, not a genuinely-empty v3.1 addition. This script only ever closes the remaining gaps (see below); it's not a from-scratch backfill.
- Added a third mode, **`--validate`**: re-resolves every entity that already has a value and compares against the real value, writing nothing. Run against the real data: **609/609 resolvable comparisons agree exactly (100%)** — confirms the lookup logic (field name, value mapping, spatial join) is correct. The other 45/654 fall outside any WOKAM polygon at this map's native 1:25,000,000 scale, same reason most blanks don't resolve either (see below) — a real coverage limit of the source map, not a script bug.

### Data corrections (2026-09-24)

- **`entity.wokam` backfilled for 3 entities** via `get_wokam.py --commit`, the first real run against the downloaded WOKAM shapefile: `entity_id` 720 and 721 (Qad_1/Qad_2, Qadisha cave, site_id 295, Lebanon) → `continuous carbonate`; `entity_id` 903 (Glas, La Vallina, site_id 324, Spain) → `discontinuous carbonate`. Of 249 entities with a blank `wokam`, these were the only 3 whose site coordinates fall inside a WOKAM polygon — the other 246 are outside any polygon at the map's 1:25M scale (left blank; see "Tooling" above for the `--validate` result backing this as a real coverage limit, not a lookup error). Verified via a full `build_db.py` rebuild: **0 FK/CHECK violations**; diff limited to exactly these 3 rows' `wokam` field.
- **`entity.copernicus_lcc` backfilled for 1 entity** — `entity_id` 903 (Glas) → `closed forest, other`, inherited from its sibling entities at site 324 (La Vallina) via `get_copernicus_lcc.py`'s new same-site propagation pass (see "Tooling" below), with **no raster tile downloaded at all**. Verified via a full `build_db.py` rebuild: **0 FK/CHECK violations**; diff limited to exactly this one row's `copernicus_lcc` field.

### Tooling (2026-09-24, continued)

- **`DS_scripts/release_finalization/get_copernicus_lcc.py`** added — second piece of the release finalization pipeline, same shape as `get_wokam.py`: `--dry-run`/`--commit`/`--validate`, schema-driven `CODE_TO_ENUM` check against `copernicus_lcc_enum` (CGLS-LC100 discrete classification codes), per-site raster value lookup via `rasterio`, only fills blank rows. New `DS_scripts/release_finalization/data/copernicus_lcc/` folder (git-ignored except its own `README.md`) for the raster tiles — download only the tile(s) covering actual SISAL site coordinates from `https://lcviewer.vito.be/`, not the whole globe. Also corrected the vault planning note's earlier claim (made by this agent, based on an incomplete spot-check) that `copernicus_lcc` was "903/903 fully populated" — it's actually 887/903, 16 blank, same gap-filling situation as `wokam`.
- **Same-site propagation, added to both `get_wokam.py` and `get_copernicus_lcc.py`**: both fields are recorded per-entity but are really site-level properties — confirmed by checking every multi-entity site with at least one known value (`wokam`: 133 sites; `copernicus_lcc`: 177 sites) for internal disagreement: **zero inconsistencies in either field**. Both scripts now run a cheap first pass that fills a blank entity directly from a sibling entity at the same site (source tagged `'sibling'` in the preview/output) *before* touching the shapefile/raster at all — the geospatial lookup only runs for whatever's left, and is skipped entirely if nothing needs it. `resolve_site_lcc()` was also made tolerant of zero downloaded raster tiles (reports every remaining site as no-coverage instead of exiting), so the sibling pass can complete and get committed independently of whether any raster data exists yet.
- Net effect run against real data: `get_wokam.py --dry-run` now reports 0 additional sibling fills (its 3 real gaps were already the only resolvable ones, and every site with a blank entity already had all its entities blank — nothing to inherit from). `get_copernicus_lcc.py --dry-run`, run **before any raster tile was downloaded**, immediately found and (`--commit`) applied the 1 sibling fill above; the other 15 blanks are correctly reported as needing tile coverage, each with its exact (lat, lon) printed so future downloads can target them precisely.

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
