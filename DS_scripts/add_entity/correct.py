"""
SISAL manual correction tool.

Apply a single field correction to the DB, re-export the affected table CSV,
and write a dated correction log to logs/ for the release paper trail.

Usage:
    python correct.py --table entity --id 903 --field iso_std --value "Vienna-PDB"
    python correct.py --table entity --id 903 --field iso_std --value "Vienna-PDB" --reason "PDB is outdated abbreviation"
    python correct.py --table site   --id 324 --field country --value "Spain"

Flags:
    --table     DB table name (default: entity)
    --id        Primary key value (entity_id for entity, site_id for site, etc.)
    --field     Column name to correct
    --value     New value (pass empty string "" to set NULL)
    --reason    Optional free-text reason (recommended for paper trail)
    --yes       Skip confirmation prompt
"""

import argparse
import sqlite3
import csv
import shutil
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import DB_PATH, CSV_DIR

LOG_DIR = Path(__file__).parent / "logs"

# Primary key column per table
_PK = {
    "entity": "entity_id",
    "site":   "site_id",
    "sample": "sample_id",
    "dating": "dating_id",
}

OK   = lambda s: f"\033[32m✅  {s}\033[0m"
WARN = lambda s: f"\033[33m⚠️   {s}\033[0m"
ERR  = lambda s: f"\033[31m❌  {s}\033[0m"


def apply_correction(table: str, pk_val: str, field: str, new_value: str,
                     reason: str, skip_confirm: bool):
    pk_col = _PK.get(table)
    if not pk_col:
        print(ERR(f"Unknown table '{table}'. Known tables: {list(_PK.keys())}"))
        return

    if not DB_PATH.exists():
        print(ERR(f"DB not found: {DB_PATH}"))
        return

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # Verify column exists
    cur.execute(f"PRAGMA table_info({table})")
    cols = {r[1] for r in cur.fetchall()}
    if field not in cols:
        print(ERR(f"Column '{field}' not found in table '{table}'."))
        con.close()
        return

    # Fetch current value
    cur.execute(f"SELECT {field} FROM {table} WHERE {pk_col}=?", (str(pk_val),))
    row = cur.fetchone()
    if row is None:
        print(ERR(f"No row found in {table} where {pk_col}={pk_val}"))
        con.close()
        return

    old_value = row[0]
    write_value = None if new_value == "" else new_value

    # Show what will happen
    print(f"\n  Table   : {table}")
    print(f"  {pk_col}  : {pk_val}")
    print(f"  Field   : {field}")
    print(f"  Old     : {old_value!r}")
    print(f"  New     : {write_value!r}")
    if reason:
        print(f"  Reason  : {reason}")
    print()

    if old_value == write_value:
        print(WARN("Old and new values are identical — nothing to do."))
        con.close()
        return

    if not skip_confirm:
        answer = input("Apply this correction? [y/N] ").strip().lower()
        if answer != "y":
            print("Aborted.")
            con.close()
            return

    # Apply
    cur.execute(
        f"UPDATE {table} SET {field}=? WHERE {pk_col}=?",
        (write_value, str(pk_val)),
    )
    con.commit()
    con.close()
    print(OK(f"DB updated: {table}.{field} for {pk_col}={pk_val}"))

    # Re-export CSV
    _update_csv(table)
    print(OK(f"CSV re-exported → {CSV_DIR / table}.csv"))

    # Write correction log
    log_path = _write_correction_log(
        table, pk_col, pk_val, field, old_value, write_value, reason
    )
    print(OK(f"Correction log → {log_path}"))


def _update_csv(table: str):
    csv_path = CSV_DIR / f"{table}.csv"
    if csv_path.exists():
        shutil.copy(csv_path, csv_path.with_suffix(".csv.bak"))
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql(f"SELECT * FROM {table}", con)
    con.close()
    df.to_csv(csv_path, index=False, quoting=csv.QUOTE_NONNUMERIC, na_rep="NA")


def _write_correction_log(table, pk_col, pk_val, field, old_value, new_value, reason):
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    log_path = LOG_DIR / f"correction_{stamp}_{table}_{pk_col}{pk_val}_{field}.md"

    lines = [
        f"# SISAL Manual Correction",
        f"",
        f"| | |",
        f"|---|---|",
        f"| **Date** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        f"| **Table** | `{table}` |",
        f"| **{pk_col}** | {pk_val} |",
        f"| **Field** | `{field}` |",
        f"| **Old value** | `{old_value}` |",
        f"| **New value** | `{new_value}` |",
        f"| **Reason** | {reason if reason else '—'} |",
        f"| **DB** | `{DB_PATH.name}` |",
        f"| **DB path** | `{DB_PATH}` |",
        f"",
        f"## Context",
        f"",
        f"Correction applied by data steward during manual review. "
        f"DB updated and `{table}.csv` re-exported to `sisalv4_csv/`.",
        f"",
    ]

    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def main():
    parser = argparse.ArgumentParser(description="SISAL manual correction tool")
    parser.add_argument("--table",  default="entity", help="DB table (default: entity)")
    parser.add_argument("--id",     required=True, help="Primary key value")
    parser.add_argument("--field",  required=True, help="Column to correct")
    parser.add_argument("--value",  required=True, help='New value (pass "" to set NULL)')
    parser.add_argument("--reason", default="", help="Reason for correction (recommended)")
    parser.add_argument("--yes",    action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    apply_correction(
        table=args.table,
        pk_val=args.id,
        field=args.field,
        new_value=args.value,
        reason=args.reason,
        skip_confirm=args.yes,
    )


if __name__ == "__main__":
    main()
