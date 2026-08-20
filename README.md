# SISALdb

Development home for **SISALv3.1** — a release-management and contributor-provenance upgrade to the SISAL (Speleothem Isotopes Synthesis and AnaLysis) database, built on top of the published SISALv3 schema.

## Status

🚧 **In development.** This is pre-release schema/migration work, not an official SISAL data release. See [CHANGELOG.md](CHANGELOG.md) for what's changed so far and what's still open.

## What's new in v3.1

SISALv3.1 adds a release-management layer on top of the existing 21-table SISALv3 schema:

- **`database_release`** — one row per citable release, identified as `SISALvX.Y — YYYY.MM Release` (SemVer generation.minor + CalVer dated snapshot — see Versioning below)
- **`person` / `entity_person` / `release_person`** — normalised contributor provenance, replacing the old free-text `entity.contact` column. `entity_person` links each speleothem record to its contact person(s); `release_person` links a release to whoever stewarded, curated, or led it
- **`code_artifact`** — DOI-citable code versions for the age-model and downsampling pipelines, so a release can point to the exact code that produced it

Full schema: [`schema/schema.dbml`](schema/schema.dbml) — paste directly into [dbdiagram.io](https://dbdiagram.io) to render the ER diagram.

## Versioning

SISAL releases are identified as `SISALvX.Y — YYYY.MM Release`:

- **`X.Y`** (database generation.minor, SemVer-style) — major = breaking schema change, minor = backwards-compatible schema addition, patch (`X.Y.Z`) = correction without new functionality.
- **`YYYY.MM`** (CalVer-style) — the specific dated snapshot, which changes independently of the schema. SISAL targets a release roughly every six months.

Example: `SISALv3.1 — 2027.07 Release` = the v3.1 schema generation, dated snapshot July 2027.

## Building the database

The compiled `.db` file isn't committed to this repo — it's 114 MB, over GitHub's 100 MB push limit, and a binary SQLite file isn't diff-friendly anyway (the schema and changelog here are the actual trackable history). To build it locally:

```bash
python3 scripts/migrate_v3_to_v3.1.py <path-to-sisalv3-csv-folder> [output-dir]
```

`<path-to-sisalv3-csv-folder>` is the per-table CSV export of the published SISALv3 release (DOI [10.5287/ora-2nanwp4rk](https://doi.org/10.5287/ora-2nanwp4rk)) — one CSV per table (`site.csv`, `entity.csv`, `sample.csv`, etc.). The script:

1. Creates all 21 published tables with real types, primary keys, foreign keys, and `CHECK` constraints (translated from the original MySQL schema), plus the new v3.1 tables.
2. Loads the CSVs with `PRAGMA foreign_keys = ON` — any row that violates a relationship fails loudly at load time rather than silently corrupting the database.
3. Backfills what's derivable from the v3 data alone: a `v3.0` baseline `database_release` row, `entity.added_in_release_id`/`last_modified_release_id`, and `person`/`entity_person` parsed from `entity.contact`.

No external dependencies — just the Python 3 standard library (`sqlite3`, `csv`).

See [`migration_log.txt`](migration_log.txt) for verification output from the most recent run, and [`CHANGELOG.md`](CHANGELOG.md) for the full list of what changed and what's still open (near-duplicate contributor names flagged for manual review, ORCID enrichment pending, etc.).

## Repo layout

| Path | Contents |
|---|---|
| `schema/schema.dbml` | Full schema, published SISALv3 tables + draft v3.1 additions |
| `scripts/migrate_v3_to_v3.1.py` | Migration script (published SISALv3 CSVs → typed SQLite) |
| `CHANGELOG.md` | Release history, one entry per `database_release` |
| `migration_log.txt` | Verification output from the latest migration run |

## Data license

The underlying SISALv3 data is published separately (DOI [10.5287/ora-2nanwp4rk](https://doi.org/10.5287/ora-2nanwp4rk)) — usage should follow the terms of that publication. This repo contains only schema/tooling, not the data itself.
