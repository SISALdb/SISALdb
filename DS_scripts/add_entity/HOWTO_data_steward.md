# SISAL DB Update — Data Steward How-To

This document explains how to import a new submission workbook into the SISAL database
(SISALv3.1), how to fix a field in an already-imported entry, and how to redo an import
after correcting the source workbook. Written for the ETH data steward and any future
steward taking over.

---

## Folder overview

```
SISALdb/                              ← this repo (git-tracked)
├── csv/                              ← THE source of truth, one CSV per table
├── schema/schema.dbml                ← full schema (tables, enums, FKs)
├── USER_scripts/
│   └── build_db.py                   ← builds a disposable sisalv3.1.db from csv/
└── DS_scripts/
    ├── add_entity/
    │   ├── add_entity.py             ← main import script (this workflow)
    │   ├── correct.py                ← retired, kept for history — see Workflow A below
    │   ├── config.py                 ← paths + workbook column mapping
    │   ├── HOWTO_data_steward.md     ← this file
    │   └── logs/                     ← paper trail (preflight logs)
    └── backfill_from_csv.py          ← single/multi-field correction tool (current)
```

`csv/` is git-tracked and diff-friendly — every import, correction, or fix is a normal
git commit. `sisalv3.1.db` is **not** committed; it's a disposable build artifact you
regenerate any time you want to query or spot-check the data:

```bash
python3 USER_scripts/build_db.py /tmp/sisal_check
```

This also re-validates every foreign key and `CHECK` constraint on load — run it after
*every* import or correction, before trusting or committing the change. All commands
below are run from the repo root unless noted.

---

## Step 0 — Auto-QC

Workbooks only reach `add_entity.py` after passing the separate Auto-QC pipeline
(`wb_check_v15.py` → `run_plots.R` → the U-Th check), conventionally renamed
`QC_passed_...` and filed under `checked submission sheet/`. `add_entity.py` asks you to
confirm this at the very start and refuses to proceed on "no" — it does not re-run
Auto-QC itself (that pipeline lives in the separate `datasteward-AutoQC` repo).

---

## Standard import workflow

### Step 1 — Dry run first (always)

```bash
python3 DS_scripts/add_entity/add_entity.py \
  --workbook "/path/to/checked submission sheet/<workbook_name>.xlsx" --dry-run
```

Answer "yes" to the Auto-QC question, then read the terminal output and the preflight
log saved to `DS_scripts/add_entity/logs/`. Check:
- Site: is it new (INSERT) or already in SISAL (REUSE)?
- Entities: how many to insert, any skipped (e.g. duplicate name at the same site)?
- Dating / sample / lamina / reference row counts, and DOI validation results
- Notes: appended to an existing site row, or a fresh row?
- Warnings and errors

Dry-run never writes anything and never asks about contacts or project assignment —
those prompts only happen at `--commit`.

### Step 2 — Commit if the preflight looks clean

```bash
python3 DS_scripts/add_entity/add_entity.py \
  --workbook "/path/to/checked submission sheet/<workbook_name>.xlsx" --commit
```

This will:
1. Re-run the preflight check (aborts if it now finds errors)
2. For each new entity, resolve its `contact` name(s) against `csv/person.csv`
   (search-or-create, same flow as `add_project.py`) and ask whether to assign the
   entity to a project
3. Ask for final confirmation, then append the new rows to the in-memory CSV tables
4. Write every touched table back to `csv/` (site, entity, entity_link_person, dating,
   sample, original_chronology, gap, hiatus, proxy tables, dating_lamina, reference,
   entity_link_reference, notes, project_link_entity, person, projects)
5. Update the preflight log in `logs/` with the commit timestamp

### Step 3 — Rebuild and verify

```bash
python3 USER_scripts/build_db.py /tmp/sisal_check
```

Must report **0 foreign key / CHECK violations**. If it doesn't, the CSVs were reverted
via `git checkout -- csv/` and the import re-investigated before trying again — never
committed to git with violations outstanding.

### Step 4 — Review the imported row(s)

Open `csv/entity.csv` and check the new rows at the bottom (or query
`sisalv3.1.db` — see [[Database Query Library]] in the Obsidian vault for ready-made
queries). Things to verify:
- `depth_ref` is `from top` (auto-filled for all v15 workbooks)
- `iso_std` uses the full name (e.g. `Vienna-PDB`, not `PDB`) — the workbook itself
  often still says `PDB`; fix via `backfill_from_csv.py` (Workflow A) if so
- Fields left blank in the workbook are blank strings in the CSV, not `NA` text
- `entity_link_person.csv` has a new row per resolved contact
- If a project was assigned, `project_link_entity.csv` has the new row

### Step 5 — Commit to git

```bash
git add csv/
git commit -m "Add entity <name> at <site> from <workbook_name>"
```

---

## Workflow A — Fix a field without re-importing

Use `backfill_from_csv.py` for this — `correct.py` (the June-era single-field tool
against a live dev database) is retired and no longer wired to this repo's `csv/`
architecture; it's left in the folder for history only, don't run it.

`backfill_from_csv.py` is keyed on `sample_id`, so it's built for `sample`/proxy-table
corrections (exactly the CL26 mineralogy fix it was used for). Build a small edits CSV
with a `sample_id` column plus `<column>` + `<column>_new` pairs for whatever you're
changing, then:

```bash
python3 DS_scripts/backfill_from_csv.py path/to/edits.csv
```

It validates every new value against the real schema (enums, numeric types) before
writing anything, flags a stale export (old value doesn't match what's currently in the
CSV), and previews every change before asking for confirmation.

**For a one-off entity-level field** (like `iso_std`) that isn't keyed by `sample_id`,
there's no dedicated tool yet — edit the row directly in `csv/entity.csv` (any text
editor or `csv` module), then rebuild and verify as in Step 3 above. If entity-level
corrections become routine, generalizing `backfill_from_csv.py` beyond `sample_id` would
be the next step (it's already schema-driven, so this is a from-list, not a
regeneration).

---

## Workflow B — Delete and redo an import

Use this when the workbook itself needs to be corrected (e.g. multiple fields are wrong)
and it's cleaner to redo the whole entity than patch fields individually.

### Step 1 — Note down the entity_id(s) to remove

Find them in `csv/entity.csv` or in the preflight log under `logs/`.

### Step 2 — Remove the rows from csv/

There's no delete tool for this (imports are additive by design) — remove the row(s) by
hand from every CSV that references the `entity_id`: `entity.csv`, `entity_link_person.csv`,
`dating.csv`, `sample.csv` (+ `original_chronology.csv`, `gap.csv`, `hiatus.csv`, and any
proxy tables keyed on those `sample_id`s), `dating_lamina.csv`, `entity_link_reference.csv`
(and `reference.csv` too, only if no other entity uses that reference), `project_link_entity.csv`
if it was assigned to a project.

If the site was also newly inserted for this workbook (check the preflight log — action
was INSERT, not REUSE) and nothing else references it, also remove its row from
`site.csv` (and `notes.csv` if a fresh note row was added for it).

Rebuild and verify (0 violations) before moving on.

### Step 3 — Fix the workbook

Open the QC-passed workbook in `checked submission sheet/` and correct the errors. Save
it (overwrite or save as a new version — if renamed, update the path in Step 4).

### Step 4 — Re-import

```bash
python3 DS_scripts/add_entity/add_entity.py \
  --workbook "/path/to/checked submission sheet/<workbook_name>.xlsx" --commit
```

The script will treat the entity as new again (since it was removed) and re-insert with
a clean row.

---

## Paper trail

Every dry-run and commit writes a log file to `DS_scripts/add_entity/logs/`:

| File pattern | When created | Contents |
|---|---|---|
| `preflight_YYYY-MM-DD_HHMM_<workbook>.md` | Every dry-run and commit | Site + entity table with proposed IDs, warnings, errors; commit timestamp added if `--commit` succeeded |

These logs are the release paper trail. When preparing a new `database_release` row,
reference the relevant preflight logs alongside the `csv/` diff for that release window.

For field-level corrections via `backfill_from_csv.py`, the paper trail is the git
commit itself (diff + commit message) rather than a separate log file — keep commit
messages specific (what changed, on which rows, why, and the source if it's a
literature-based correction, as in the CL26 mineralogy fix).

---

## Quick reference

| Task | Command |
|---|---|
| Dry run | `python3 DS_scripts/add_entity/add_entity.py --workbook "X.xlsx" --dry-run` |
| Commit | `python3 DS_scripts/add_entity/add_entity.py --workbook "X.xlsx" --commit` |
| Rebuild + verify | `python3 USER_scripts/build_db.py /tmp/sisal_check` |
| Fix sample/proxy field(s) | `python3 DS_scripts/backfill_from_csv.py path/to/edits.csv` |
| Check a row (after rebuild) | `sqlite3 /tmp/sisal_check/sisalv3.1.db "SELECT * FROM entity WHERE entity_id=903;"` |
