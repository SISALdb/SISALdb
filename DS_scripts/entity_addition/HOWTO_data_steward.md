# SISAL DB Update — Data Steward How-To

This document explains how to import a new submission workbook into the SISAL database,
how to fix errors in an already-imported entry, and how to redo an import after correcting
the source workbook. Written for the ETH data steward and any future steward taking over.

---

## Folder overview

```
SQL_and_AgeModel/
├── db_update_claude/               ← scripts (run all commands from here)
│   ├── importer.py                 ← main import script
│   ├── correct.py                  ← manual field correction tool
│   ├── config.py                   ← paths (DB, CSV folder)
│   ├── HOWTO_data_steward.md       ← this file
│   └── logs/                       ← paper trail (preflight + correction logs)
├── checked submission sheet/       ← QC-passed workbooks ready to import
├── sisalv4_update_dev/
│   ├── sisalv4_dev.db              ← working SQLite database (the update target)
│   └── sisalv4_csv/                ← full CSV export of all tables (updated after each import)
└── sisalv3_database_mysql_csv_published/
    └── sisalv3_csv/                ← original published v3 CSVs — DO NOT TOUCH
```

All commands below are run from `db_update_claude/`.

---

## Standard import workflow

### Step 1 — Dry run first (always)

```bash
python importer.py --workbook "../checked submission sheet/<workbook_name>.xlsx" --dry-run
```

Read the terminal output and the preflight log that is saved to `logs/`.
Check:
- Site: is it new (INSERT) or already in SISAL (REUSE)?
- Entities: how many to insert, any skipped?
- Warnings and errors

### Step 2 — Commit if the preflight looks clean

```bash
python importer.py --workbook "../checked submission sheet/<workbook_name>.xlsx" --commit
```

This will:
1. Re-run the preflight check
2. Insert site (if new) and all entities into `sisalv4_dev.db`
3. Export all 21 tables to `sisalv4_csv/`
4. Save a preflight log to `logs/preflight_YYYY-MM-DD_<workbook>.md` (with commit timestamp)

### Step 3 — Review the imported row(s)

Open `sisalv4_csv/entity.csv` and check the new rows at the bottom.
Things to verify:
- `depth_ref` is `from top` ✓ (auto-filled for all v15 workbooks)
- `iso_std` uses the full name (e.g. `Vienna-PDB`, not `PDB`)
- Fields that were left blank in the workbook show as `NA`
- `None-not applicable` entries are replaced with `NA`

---

## Workflow A — Fix a single field without re-importing

Use this when you spot one (or a few) errors in an already-imported row.

```bash
python correct.py --id <entity_id> --field <column_name> --value "<new value>" --reason "<why>"
```

**Example — wrong isotope standard:**
```bash
python correct.py --id 903 --field iso_std --value "Vienna-PDB" \
  --reason "PDB is outdated abbreviation; corrected to Vienna-PDB (VPDB)"
```

This will:
1. Show you the old and new value and ask for confirmation (press `y`)
2. Update `sisalv4_dev.db`
3. Re-export `entity.csv` to `sisalv4_csv/`
4. Write a correction log to `logs/correction_YYYY-MM-DD_entity_<id>_<field>.md`

To skip the confirmation prompt (e.g. in a scripted workflow):
```bash
python correct.py --id 903 --field iso_std --value "Vienna-PDB" --reason "..." --yes
```

To set a field back to empty (NULL / NA):
```bash
python correct.py --id 903 --field data_DOI_URL --value "" --reason "DOI not yet available"
```

**For site corrections**, add `--table site`:
```bash
python correct.py --table site --id 324 --field country --value "Spain" --reason "..."
```

---

## Workflow B — Delete and redo an import

Use this when the workbook itself needs to be corrected (e.g. multiple fields are wrong,
or the data steward wants to fix the source file before re-importing).

### Step 1 — Note down the entity_id(s) to remove

Find them in `sisalv4_csv/entity.csv` or in the preflight log.

### Step 2 — Delete from the database

```bash
sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" "DELETE FROM entity WHERE entity_id=<id>;"
```

For multiple entities:
```bash
sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" \
  "DELETE FROM entity WHERE entity_id IN (903, 904, 905);"
```

If the site was also newly inserted (check the preflight log — action was INSERT, not REUSE):
```bash
sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" "DELETE FROM site WHERE site_id=<id>;"
```

### Step 3 — Fix the workbook

Open the QC-passed workbook in `checked submission sheet/` and correct the errors.
Save it (overwrite or save as a new version — if you rename it, update the path in Step 4).

### Step 4 — Re-import

```bash
python importer.py --workbook "../checked submission sheet/<workbook_name>.xlsx" --commit
```

The script will detect the same entity name as new (since you deleted it) and re-insert
with a clean row — `None-not applicable` → NA, `depth_ref` → `from top`, etc.

### Step 5 — Re-apply any known corrections

If you had previously fixed fields via `correct.py` (e.g. `iso_std`), re-apply them now:
```bash
python correct.py --id <new_entity_id> --field iso_std --value "Vienna-PDB" \
  --reason "Corrected after redo — workbook also updated" --yes
```

---

## Paper trail

Every action generates a log file in `logs/`:

| File pattern | When created | Contents |
|---|---|---|
| `preflight_YYYY-MM-DD_HHMM_<workbook>.md` | Every dry-run and commit | Site + entity table with proposed IDs, warnings, errors, commit timestamp |
| `correction_YYYY-MM-DD_HHMM_entity_<id>_<field>.md` | Every `correct.py` run | Old value → new value, reason, timestamp |

These logs are the release paper trail. When preparing a new DB release, include the full
`logs/` folder alongside the CSV export and the DB file.

---

## Quick reference

| Task | Command |
|---|---|
| Dry run | `python importer.py --workbook "../checked submission sheet/X.xlsx" --dry-run` |
| Commit | `python importer.py --workbook "../checked submission sheet/X.xlsx" --commit` |
| Fix one field | `python correct.py --id 903 --field iso_std --value "Vienna-PDB" --reason "..."` |
| Fix site field | `python correct.py --table site --id 324 --field country --value "Spain" --reason "..."` |
| Set field to NA | `python correct.py --id 903 --field data_DOI_URL --value "" --reason "..."` |
| Delete entity | `sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" "DELETE FROM entity WHERE entity_id=903;"` |
| Delete site | `sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" "DELETE FROM site WHERE site_id=324;"` |
| Check a row | `sqlite3 "../sisalv4_update_dev/sisalv4_dev.db" "SELECT * FROM entity WHERE entity_id=903;"` |
