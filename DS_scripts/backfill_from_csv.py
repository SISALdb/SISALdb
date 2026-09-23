"""Backfill edited column values from a review CSV back into the real
source-of-truth CSVs (csv/sample.csv, csv/d18O.csv, etc.) -- keyed by sample_id.

Workflow this supports: run a query in DBeaver (or anywhere) that includes
`sample_id` and whatever columns you're reviewing, save/export it as a CSV,
then for each column you actually want to change, add a second column with
the same name plus "_new" (e.g. `mineralogy` + `mineralogy_new`) and fill in
the new value only on rows that need to change -- leave `_new` blank for "no
change" on that row. Run this script against that file.

Input CSV requirements:
  - Must have a `sample_id` column.
  - A column is treated as an edit target only when BOTH `<column>` and
    `<column>_new` are present in the header -- the plain column isn't just
    decorative, it's used to double check the edits CSV isn't stale (see
    below) and to show a clean before/after in the preview.
  - A blank `<column>_new` cell means "no change" for that row -- it's never
    written as an empty value.
  - Any other column is ignored entirely.

Fully schema-driven, no hardcoded column-to-table map: which real CSV a given
column lives in, and whether it's a controlled-vocabulary (enum) or numeric
field, are both worked out by parsing schema/schema.dbml directly. If a
column exists in more than one table (or in none), the script says so and
refuses to guess. Every non-blank "_new" value is validated against the
schema (exact enum match, or parseable number) before anything is written --
on any violation the whole run is refused and the exact row/value is named,
rather than partially applying the good rows.

One more safety check: if the `<column>` (old) value in your edits CSV
doesn't match what's currently in the real source-of-truth CSV, that's a
sign your exported file is stale (something else changed the data in the
meantime) -- flagged as a warning in the preview rather than blocking, since
you might have a good reason, but worth reading before confirming.

Run from the repo root:
    uv run python DS_scripts/backfill_from_csv.py path/to/edits.csv
    (or: python3 DS_scripts/backfill_from_csv.py path/to/edits.csv)

After running, rebuild and verify before committing anyway -- schema-driven
validation here only checks enum/numeric correctness, not foreign keys or
anything else the real schema enforces:
    python3 USER_scripts/build_db.py /tmp/sisal_check
"""
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = REPO_ROOT / "csv"
SCHEMA_DBML = REPO_ROOT / "schema" / "schema.dbml"

KEY_COLUMN = "sample_id"  # every table this script can target must have this column


# ---------------------------------------------------------------- DBML parsing

def _extract_braced_blocks(text, keyword):
    """Find every `<keyword> <name> { ... }` block in `text`, matching braces
    by depth so a nested sub-block (this schema's composite-key
    `indexes { (a, b) [pk] }`) doesn't prematurely close the outer block.
    Returns [(name, body_text), ...]."""
    blocks = []
    for m in re.finditer(rf"{keyword}\s+(\S+)\s*\{{", text):
        name = m.group(1)
        i = m.end()  # just past the opening brace
        depth = 1
        start = i
        while depth > 0 and i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        blocks.append((name, text[start : i - 1]))
    return blocks


def parse_dbml(path):
    """Returns (enums, tables):
      enums:  {enum_name: [value, ...]}
      tables: {table_name: {column_name: type_string}}
    Good enough for this repo's own schema.dbml -- not a general DBML parser
    (doesn't need to be; this repo owns both the schema and the file)."""
    text = path.read_text(encoding="utf-8")

    enums = {}
    for name, body in _extract_braced_blocks(text, "Enum"):
        enums[name] = re.findall(r'"([^"]*)"', body)

    tables = {}
    for name, body in _extract_braced_blocks(text, "Table"):
        columns = {}
        for line in body.splitlines():
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("Note"):
                continue
            if line.startswith("indexes") or line.startswith("("):
                continue  # composite-key sub-block content, not a column
            m = re.match(r"^(\w+)\s+(\S+)", line)
            if m:
                columns[m.group(1)] = m.group(2)
        tables[name] = columns

    return enums, tables


def find_target_table(tables, base_col):
    """Which table(s) have both KEY_COLUMN and base_col as columns. Returns
    the single matching table name, or raises with a clear message if zero
    or more than one match."""
    candidates = [
        t for t, cols in tables.items()
        if KEY_COLUMN in cols and base_col in cols
    ]
    if not candidates:
        raise ValueError(
            f"'{base_col}' isn't a column on any table that also has '{KEY_COLUMN}' -- "
            f"check the column name (it must match schema/schema.dbml exactly)."
        )
    if len(candidates) > 1:
        raise ValueError(
            f"'{base_col}' exists on more than one table with '{KEY_COLUMN}': "
            f"{', '.join(sorted(candidates))} -- ambiguous, can't pick automatically."
        )
    return candidates[0]


def validate_new_value(tables, enums, table, column, value):
    """Returns None if `value` is acceptable for `table.column`, else a short
    reason string explaining why it isn't."""
    col_type = tables[table][column]
    if col_type in enums:
        allowed = enums[col_type]
        if value not in allowed:
            allowed_str = ", ".join(f"'{v}'" for v in allowed)
            return f"'{value}' is not a valid {column} -- must be exactly one of: {allowed_str}"
        return None
    if col_type in ("int", "double", "float"):
        try:
            float(value)
        except ValueError:
            return f"'{value}' is not a valid number for {column} (schema type: {col_type})"
        return None
    return None  # text/date/varchar/etc. -- no specific rule to check here


# ---------------------------------------------------------------- CSV I/O

def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_csv(path, fieldnames, rows):
    # Target CSVs (sample.csv, d18O.csv, etc.) don't carry a UTF-8 BOM today
    # (unlike person.csv/projects.csv, which do for Excel's benefit with
    # accented names) -- write plain utf-8 to match their existing convention.
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 DS_scripts/backfill_from_csv.py path/to/edits.csv")
        sys.exit(1)

    edits_path = Path(sys.argv[1]).expanduser()
    if not edits_path.exists():
        print(f"File not found: {edits_path}")
        sys.exit(1)

    enums, tables = parse_dbml(SCHEMA_DBML)

    edit_fields, edit_rows = read_csv(edits_path)
    if KEY_COLUMN not in edit_fields:
        print(f"The edits CSV must have a '{KEY_COLUMN}' column.")
        sys.exit(1)

    # A column is an edit target only when both <col> and <col>_new are present.
    edit_pairs = []  # [(base_col, new_col), ...]
    for f in edit_fields:
        if f.endswith("_new"):
            base = f[: -len("_new")]
            if base in edit_fields:
                edit_pairs.append((base, f))
    if not edit_pairs:
        print("No '<column>' + '<column>_new' pairs found in the header -- nothing to do.")
        sys.exit(1)

    # Resolve each base column to exactly one target table, up front.
    resolved = {}  # base_col -> table_name
    errors = []
    for base, _new in edit_pairs:
        try:
            resolved[base] = find_target_table(tables, base)
        except ValueError as e:
            errors.append(str(e))
    if errors:
        print("Can't resolve every edited column to a table -- fix these and rerun:")
        for e in errors:
            print(f"  {e}")
        sys.exit(1)

    print("Resolved columns:")
    for base, table in resolved.items():
        print(f"  {base}  ->  {table}.csv")

    # Validate every non-blank _new value up front, before touching anything.
    violations = []
    for row_num, row in enumerate(edit_rows, start=2):  # +1 header, +1 for 1-indexing
        sample_id = row[KEY_COLUMN].strip()
        for base, new_col in edit_pairs:
            new_val = row[new_col].strip()
            if not new_val:
                continue
            table = resolved[base]
            reason = validate_new_value(tables, enums, table, base, new_val)
            if reason:
                violations.append(f"  row {row_num} (sample_id {sample_id or '?'}): {reason}")
    if violations:
        print(f"\n{len(violations)} invalid value(s) found -- fix these in {edits_path.name} and rerun:")
        for v in violations:
            print(v)
        sys.exit(1)

    # Group requested edits by target table/file: table -> {sample_id: {column: new_value}}
    edits_by_table = {}
    stale_warnings = []
    for row in edit_rows:
        sample_id = row[KEY_COLUMN].strip()
        if not sample_id:
            continue
        for base, new_col in edit_pairs:
            new_val = row[new_col].strip()
            if not new_val:
                continue
            edits_by_table.setdefault(resolved[base], {}).setdefault(sample_id, {})[base] = (new_val, row.get(base, "").strip())

    if not edits_by_table:
        print("\nNo non-blank '_new' values found -- nothing to do.")
        return

    # --- preview ---
    print("\n=== Planned changes ===")
    total_changes = 0
    loaded = {}  # table_name -> (fields, rows, by_pk)
    for table, by_id in edits_by_table.items():
        csv_path = CSV_DIR / f"{table}.csv"
        if not csv_path.exists():
            print(f"\n{table}.csv: FILE NOT FOUND at {csv_path} -- skipping all edits for this table")
            continue
        t_fields, t_rows = read_csv(csv_path)
        by_pk = {r[KEY_COLUMN]: r for r in t_rows}
        loaded[table] = (t_fields, t_rows, by_pk)

        print(f"\n{table}.csv:")
        for pk_val, col_updates in by_id.items():
            row = by_pk.get(pk_val)
            if row is None:
                print(f"  {KEY_COLUMN} {pk_val}: NOT FOUND in {table}.csv -- skipping")
                continue
            for col, (new_val, edits_csv_old_val) in col_updates.items():
                real_old_val = row.get(col, "")
                if edits_csv_old_val and edits_csv_old_val != real_old_val:
                    stale_warnings.append(
                        f"  {KEY_COLUMN} {pk_val}, {col}: edits CSV shows old value "
                        f"'{edits_csv_old_val}', but the real current value is "
                        f"'{real_old_val}' -- your export may be stale."
                    )
                if real_old_val == new_val:
                    print(f"  {KEY_COLUMN} {pk_val}: {col} already '{new_val}' -- no-op")
                else:
                    print(f"  {KEY_COLUMN} {pk_val}: {col}  '{real_old_val}' -> '{new_val}'")
                    total_changes += 1

    if stale_warnings:
        print(f"\n{len(stale_warnings)} possibly-stale row(s) -- the real value has changed since your export:")
        for w in stale_warnings:
            print(w)

    if total_changes == 0:
        print("\nNo actual changes (target values already match, files missing, or rows not found) -- nothing to write.")
        return

    confirm = input(f"\nApply {total_changes} change(s) to the CSV(s) above? (y/n) [n]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Aborted -- nothing written.")
        return

    # --- apply ---
    for table, by_id in edits_by_table.items():
        if table not in loaded:
            continue
        t_fields, t_rows, by_pk = loaded[table]
        for pk_val, col_updates in by_id.items():
            row = by_pk.get(pk_val)
            if row is None:
                continue
            for col, (new_val, _old) in col_updates.items():
                row[col] = new_val
        write_csv(CSV_DIR / f"{table}.csv", t_fields, t_rows)
        print(f"Wrote {table}.csv")

    print("\nDone. Next: rebuild and verify before committing:")
    print("  python3 USER_scripts/build_db.py /tmp/sisal_check")


if __name__ == "__main__":
    main()
