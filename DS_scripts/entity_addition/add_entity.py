"""
SISAL DB update -- add a new site/entity (and its dating, sample, lamina,
reference, notes data) from a QC-passed submission workbook. SISALv3.1,
CSV-first: reads from and writes to this repo's csv/ (the real source of
truth), not a live database.

Usage:
    python add_entity.py --workbook <path.xlsx> [--dry-run] [--commit]

Flags:
    --dry-run   (default) Run all checks and print a pre-flight report.
                Nothing is written to csv/.
    --commit    After a clean dry-run, write to csv/.

Workflow:
    1. Confirm the workbook has already passed Auto-QC (wb_check_v15.py, a
       separate repo) -- this script asks and refuses to proceed on 'no'.
    2. Always run --dry-run first and review the report.
    3. If the report looks good, re-run with --commit.
    4. Rebuild and verify before committing to git:
         python3 USER_scripts/build_db.py /tmp/sisal_check
"""

import argparse
import csv as csv_module
import re
import sys
import urllib.request
import urllib.error
import urllib.parse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import CSV_DIR, ENTITY_WORKBOOK_COLS, SCHEMA_DBML

LOG_DIR = Path(__file__).parent / "logs"

# Dating columns present in workbook but not in dating.csv (skip on insert)
_DATING_WORKBOOK_ONLY = {"entity_name", "chem_year"}

# dating.csv columns filled by the age-model pipeline, not the workbook (insert as blank)
_DATING_AGEMODEL_COLS = {
    "date_used_lin_interp", "date_used_lin_reg", "date_used_Bchron",
    "date_used_Bacon", "date_used_OxCal", "date_used_copRa", "date_used_StalAge",
}

# original_chronology.csv columns that come from the Sample data sheet
_ORIG_CHRON_COLS = {
    "interp_age", "interp_age_uncert_pos", "interp_age_uncert_neg",
    "age_model_type", "ann_lam_check", "dep_rate_check",
}

# Proxy table -> (measurement col, precision col)
_PROXY_TABLES = {
    "d18O":        ("d18O_measurement",        "d18O_precision"),
    "d13C":        ("d13C_measurement",        "d13C_precision"),
    "Sr_Ca":       ("Sr_Ca_measurement",       "Sr_Ca_precision"),
    "Mg_Ca":       ("Mg_Ca_measurement",       "Mg_Ca_precision"),
    "Ba_Ca":       ("Ba_Ca_measurement",       "Ba_Ca_precision"),
    "U_Ca":        ("U_Ca_measurement",        "U_Ca_precision"),
    "P_Ca":        ("P_Ca_measurement",        "P_Ca_precision"),
    "Sr_isotopes": ("Sr_isotopes_measurement", "Sr_isotopes_precision"),
}

ROLE_CHOICES = [
    "release_steward", "data_curator", "workflow_developer",
    "project_lead", "data_contributor",
]


# -- Colour helpers (terminal output) ------------------------------------------
OK   = lambda s: f"\033[32m✅  {s}\033[0m"
WARN = lambda s: f"\033[33m⚠️   {s}\033[0m"
ERR  = lambda s: f"\033[31m❌  {s}\033[0m"
INFO = lambda s: f"    {s}"


# -- CSV I/O --------------------------------------------------------------------
def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv_module.DictReader(f)
        return reader.fieldnames, list(reader)


def write_csv(path, fieldnames, rows, use_bom=False):
    # Every existing table CSV in this repo is plain utf-8 (no BOM) except
    # person.csv/projects.csv, which carry a BOM for Excel's benefit with
    # accented names -- match that convention per file.
    encoding = "utf-8-sig" if use_bom else "utf-8"
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv_module.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_all(tables):
    """tables: list of table names (no .csv). Returns {table: (fields, rows)}."""
    loaded = {}
    for t in tables:
        loaded[t] = read_csv(CSV_DIR / f"{t}.csv")
    return loaded


def next_id(rows, id_col):
    ids = [int(r[id_col]) for r in rows if r.get(id_col, "").strip()]
    return (max(ids) if ids else 0) + 1


def normalise(s: str) -> str:
    """Lowercase, strip spaces -- used for fuzzy name matching."""
    return re.sub(r"\s+", "", str(s).lower().strip())


# Values from the workbook that should be stored as blank/NULL
_NULL_SENTINELS = {"none-not applicable", "nan", "none", "n/a", ""}


def clean_val(val) -> str:
    """Normalise a workbook cell value for CSV insert. Returns "" for missing
    or placeholder values, otherwise a stripped string."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = str(val).strip()
    if s.lower() in _NULL_SENTINELS:
        return ""
    return s


def prompt(text, required=True, default=None):
    suffix = f" [{default}]" if default is not None else ""
    while True:
        val = input(f"{text}{suffix}: ").strip()
        if not val and default is not None:
            return default
        if not val and not required:
            return ""
        if val:
            return val
        print("  This is required -- please enter a value.")


def prompt_yes_no(text, default_yes=True):
    default = "y" if default_yes else "n"
    while True:
        val = input(f"{text} (y/n) [{default}]: ").strip().lower()
        if not val:
            val = default
        if val in ("y", "yes"):
            return True
        if val in ("n", "no"):
            return False
        print("  Please answer y or n.")


def prompt_choice(text, choices, default=None):
    choice_str = "/".join(choices)
    while True:
        val = prompt(f"{text} ({choice_str})", required=default is None, default=default)
        val = val.lower()
        if val in choices:
            return val
        print(f"  Please enter one of: {choice_str}")


# -- Workbook reader --------------------------------------------------------------
def read_workbook(wb_path: Path) -> dict:
    """Read Site metadata and Entity metadata sheets; row 2 is the header."""
    drop_sheets = {
        "INSTRUCTIONS", "SISALv3_database_structure", "Entity in SISAL ",
        "Notes", "Drop-down Lists",  # Notes is read separately (no header row)
    }
    xls = pd.ExcelFile(wb_path)
    data = {}
    for sheet in xls.sheet_names:
        if sheet in drop_sheets:
            continue
        df = pd.read_excel(xls, sheet_name=sheet, header=1)  # row index 1 = row 2
        df = df.loc[:, df.columns.notna()]
        df = df.dropna(how="all").reset_index(drop=True)
        if not df.empty:
            data[sheet] = df
    return data


# -- DBML parsing (schema-driven enum validation, same pattern as backfill_from_csv.py) --
def _extract_braced_blocks(text, keyword):
    """Find every `<keyword> <name> { ... }` block in `text`, matching braces
    by depth so a nested sub-block (this schema's composite-key
    `indexes { (a, b) [pk] }`) doesn't prematurely close the outer block.
    Returns [(name, body_text), ...]."""
    blocks = []
    for m in re.finditer(rf"{keyword}\s+(\S+)\s*\{{", text):
        name = m.group(1)
        i = m.end()
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
    """Returns (enums, tables): enums {enum_name: [value, ...]},
    tables {table_name: {column_name: type_string}}."""
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
                continue
            m = re.match(r"^(\w+)\s+(\S+)", line)
            if m:
                columns[m.group(1)] = m.group(2)
        tables[name] = columns

    return enums, tables


def _normalize_enum_value(v: str) -> str:
    """Strip trailing punctuation/whitespace and casefold -- catches the
    'looks the same but isn't' class of typo (trailing period, stray space,
    wrong case) without treating a genuinely different value as a match."""
    return re.sub(r"[\s.]+$", "", v.strip()).casefold()


def _find_near_miss(value, allowed):
    norm_val = _normalize_enum_value(value)
    for a in allowed:
        if a != value and _normalize_enum_value(a) == norm_val:
            return a
    return None


def check_row_enums(row, table_name, tables, enums, row_label):
    """Validate every enum-typed column present in `row` against the real
    controlled vocabulary in schema.dbml. A value that fails an exact match
    would fail the table's CHECK constraint at build_db.py time regardless --
    so any mismatch is reported as an error here, before --commit ever writes
    it. When the mismatch is only a trailing-punctuation/whitespace/case
    difference from a valid value, the message names the exact fix; otherwise
    it lists the full allowed set."""
    messages = []
    cols = tables.get(table_name, {})
    for col, col_type in cols.items():
        if col_type not in enums or col not in row:
            continue
        value = clean_val(row.get(col))
        if not value or value in enums[col_type]:
            continue
        near = _find_near_miss(value, enums[col_type])
        if near:
            messages.append((ERR,
                f"{row_label}: '{col}' = '{value}' looks like a typo of the valid value "
                f"'{near}' -- will fail the {col_type} CHECK constraint as written. "
                f"Fix in the workbook (or the CSV) before --commit."))
        else:
            allowed_str = ", ".join(f"'{v}'" for v in enums[col_type])
            messages.append((ERR,
                f"{row_label}: '{col}' = '{value}' is not a valid value -- must be exactly one of: {allowed_str}"))
    return messages


# -- Pre-flight checks ------------------------------------------------------------
def check_site(site_rows, site_row: pd.Series, tables=None, enums=None) -> dict:
    """
    Returns: {action: 'insert'|'reuse', site_id: int, messages: [(level, text), ...]}
    """
    name = site_row["site_name"]
    messages = []

    existing = next(
        (r for r in site_rows if normalise(r["site_name"]) == normalise(name)), None
    )

    if existing:
        sid = int(existing["site_id"])
        messages.append((OK, f"Site '{name}' already in SISAL (site_id={sid}) -> will reuse, no INSERT"))
        try:
            if abs(float(existing["latitude"]) - float(site_row["latitude"])) > 0.01:
                messages.append((WARN, f"  Latitude mismatch: CSV={existing['latitude']}, workbook={site_row['latitude']}"))
            if abs(float(existing["longitude"]) - float(site_row["longitude"])) > 0.01:
                messages.append((WARN, f"  Longitude mismatch: CSV={existing['longitude']}, workbook={site_row['longitude']}"))
        except (TypeError, ValueError):
            pass
        if tables is not None:
            messages.extend(check_row_enums(site_row, "site", tables, enums, f"Site '{name}'"))
        return {"action": "reuse", "site_id": sid, "messages": messages}
    else:
        new_id = next_id(site_rows, "site_id")
        messages.append((OK, f"Site '{name}' is NEW -> will INSERT with site_id={new_id}"))
        if tables is not None:
            messages.extend(check_row_enums(site_row, "site", tables, enums, f"Site '{name}'"))
        return {"action": "insert", "site_id": new_id, "messages": messages}


def check_entity(entity_rows, entity_row: pd.Series, site_id: int, tables=None, enums=None) -> dict:
    """
    Returns: {action: 'insert'|'skip', entity_id: int, persist_id: str,
              _name: str, messages: [...]}
    """
    name = str(entity_row["entity_name"]).strip()
    one_and_only = str(entity_row.get("one_and_only", "yes")).lower().strip()
    status_info = str(entity_row.get("entity_status_info", "not applicable")).lower().strip()
    status_notes = str(entity_row.get("entity_status_notes", "")).strip()
    messages = []

    clean = re.sub(r"[-_]\d{4}$", "", name)
    persist_id = f"{site_id}-{re.sub(r'[-]', '', clean).upper()}"

    existing_same_name = [
        r for r in entity_rows
        if r["site_id"] == str(site_id) and normalise(r["entity_name"]) == normalise(name)
    ]
    existing_persist = [r for r in entity_rows if r["persist_id"] == persist_id]

    if existing_same_name:
        ids = ", ".join(r["entity_id"] for r in existing_same_name)
        statuses = ", ".join(r["entity_status"] for r in existing_same_name)
        messages.append((WARN,
            f"Entity '{name}' already exists at site_id={site_id} "
            f"(entity_id={ids}, status={statuses})"))
        messages.append((ERR,
            f"  -> Cannot insert duplicate. Check 'one_and_only' and 'entity_status_info' "
            f"in workbook. Skipping this entity."))
        return {"action": "skip", "entity_id": int(existing_same_name[0]["entity_id"]),
                "persist_id": persist_id, "_name": name, "messages": messages}

    if existing_persist:
        messages.append((WARN,
            f"persist_id '{persist_id}' already exists (entity_id={existing_persist[0]['entity_id']}) "
            f"-- will still insert entity with this persist_id (allowed for superseded chains)"))

    new_id = next_id(entity_rows, "entity_id")

    if one_and_only == "yes":
        messages.append((OK,
            f"Entity '{name}' is NEW, one_and_only=yes -> INSERT as 'current' "
            f"(entity_id={new_id}, persist_id={persist_id})"))
    elif status_info == "current":
        messages.append((OK,
            f"Entity '{name}' is NEW, supersedes entity_id(s): {status_notes} "
            f"-> INSERT as 'current', those IDs will be set to 'superseded'"))
        if status_notes:
            for old_id in status_notes.split(";"):
                old_id = old_id.strip()
                match = next((r for r in entity_rows if r["entity_id"] == old_id), None)
                if match:
                    messages.append((INFO, f"  Will supersede entity_id={old_id} ('{match['entity_name']}', currently '{match['entity_status']}')"))
                else:
                    messages.append((WARN, f"  entity_id={old_id} listed in status_notes but NOT found"))
    else:
        messages.append((OK,
            f"Entity '{name}' is NEW (status_info='{status_info}') "
            f"-> INSERT (entity_id={new_id}, persist_id={persist_id})"))

    if tables is not None:
        messages.extend(check_row_enums(entity_row, "entity", tables, enums, f"Entity '{name}'"))

    return {"action": "insert", "entity_id": new_id, "persist_id": persist_id,
            "_name": name, "messages": messages}


def _entity_id_for_name(entity_rows, entity_results, site_id, name):
    """Resolve entity_name -> entity_id, checking already-committed rows first,
    then this run's pending inserts."""
    match = next(
        (r for r in entity_rows if r["site_id"] == str(site_id) and r["entity_name"] == name),
        None,
    )
    if match:
        return int(match["entity_id"])
    pending = [er for er in entity_results if er["action"] == "insert" and er.get("_name") == name]
    return pending[0]["entity_id"] if pending else None


def check_dating(dating_rows, df_dating: pd.DataFrame, site_id, entity_rows, entity_results) -> dict:
    messages, errors = [], 0
    entity_map = {}
    for name in df_dating["entity_name"].dropna().unique():
        name = str(name).strip()
        eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
        if eid is not None:
            entity_map[name] = eid
        else:
            messages.append((ERR, f"Dating: entity '{name}' not found and not pending insert -- cannot assign entity_id"))
            errors += 1

    max_id = next_id(dating_rows, "dating_id") - 1
    dating_ids = list(range(max_id + 1, max_id + 1 + len(df_dating)))
    messages.append((OK, f"Dating: {len(df_dating)} rows, entity_id(s) {sorted(set(entity_map.values()))} -> dating_id {dating_ids[0]}-{dating_ids[-1]}"))
    return {"entity_map": entity_map, "dating_ids": dating_ids, "messages": messages, "errors": errors}


def check_sample(sample_rows, df_sample: pd.DataFrame, site_id, entity_rows, entity_results) -> dict:
    messages, errors = [], 0
    entity_map = {}
    for name in df_sample["entity_name"].dropna().unique():
        name = str(name).strip()
        eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
        if eid is not None:
            entity_map[name] = eid
        else:
            messages.append((ERR, f"Sample: entity '{name}' not found and not pending insert"))
            errors += 1

    max_id = next_id(sample_rows, "sample_id") - 1
    n = len(df_sample)
    sample_ids = list(range(max_id + 1, max_id + 1 + n))

    proxy_counts = {}
    for table, (meas_col, _) in _PROXY_TABLES.items():
        if meas_col in df_sample.columns:
            proxy_counts[table] = int(df_sample[meas_col].notna().sum())

    hiatus_count = int(df_sample["hiatus"].notna().sum()) if "hiatus" in df_sample.columns else 0
    gap_count = int(df_sample["gap"].notna().sum()) if "gap" in df_sample.columns else 0

    messages.append((OK, f"Sample: {n} rows -> sample_id {sample_ids[0]}-{sample_ids[-1]}"))
    proxy_summary = ", ".join(f"{t}={c}" for t, c in proxy_counts.items() if c > 0)
    if proxy_summary:
        messages.append((OK, f"  Proxy rows: {proxy_summary}"))
    if hiatus_count:
        messages.append((OK, f"  Hiatus rows: {hiatus_count}"))
    if gap_count:
        messages.append((OK, f"  Gap rows: {gap_count}"))

    return {"entity_map": entity_map, "sample_ids": sample_ids, "proxy_counts": proxy_counts,
            "hiatus_count": hiatus_count, "gap_count": gap_count, "messages": messages, "errors": errors}


def check_lamina(lamina_rows, df_lamina: pd.DataFrame, site_id, entity_rows, entity_results) -> dict:
    messages, errors = [], 0
    entity_map = {}
    for name in df_lamina["entity_name"].dropna().unique():
        name = str(name).strip()
        eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
        if eid is not None:
            entity_map[name] = eid
        else:
            messages.append((ERR, f"Lamina: entity '{name}' not found and not pending insert"))
            errors += 1

    max_id = next_id(lamina_rows, "dating_lamina_id") - 1
    n = len(df_lamina)
    lamina_ids = list(range(max_id + 1, max_id + 1 + n))
    messages.append((OK, f"Lamina: {n} rows, entity_id(s) {sorted(set(entity_map.values()))} -> dating_lamina_id {lamina_ids[0]}-{lamina_ids[-1]}"))
    return {"entity_map": entity_map, "lamina_ids": lamina_ids, "messages": messages, "errors": errors}


# -- DOI validation via CrossRef -------------------------------------------------
def _validate_doi(doi: str, citation) -> tuple:
    msgs = []
    doi_clean = doi.strip().removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi_clean, safe='/')}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SISAL-importer/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            msgs.append((ERR, f"DOI not found in CrossRef: {doi}"))
            return False, msgs
        msgs.append((WARN, f"CrossRef returned HTTP {e.code} for {doi} -- cannot validate"))
        return True, msgs
    except Exception as e:
        msgs.append((WARN, f"CrossRef lookup failed ({type(e).__name__}) -- skipping DOI validation"))
        return True, msgs

    work = payload.get("message", {})
    titles = work.get("title", [])
    cr_title = titles[0] if titles else ""
    if cr_title and citation:
        key_words = re.sub(r"[^\w\s]", "", cr_title.lower()).split()[:6]
        citation_lower = citation.lower()
        matched = sum(1 for w in key_words if w in citation_lower)
        if matched < max(2, len(key_words) // 2):
            msgs.append((WARN, f"DOI resolves but title may not match citation.\n    CrossRef title : '{cr_title[:80]}'\n    Citation snippet: '{(citation or '')[:80]}'"))
        else:
            msgs.append((OK, f"DOI validated - CrossRef title: '{cr_title[:80]}'"))
    else:
        msgs.append((OK, f"DOI resolves (no title to cross-check): {doi}"))

    authors = work.get("author", [])
    if authors and citation:
        first_family = authors[0].get("family", "")
        if first_family and first_family.lower() not in citation.lower():
            msgs.append((WARN, f"First author '{first_family}' from CrossRef not found in citation text"))

    return True, msgs


def check_references(reference_rows, df_refs: pd.DataFrame, entity_rows, entity_results, site_id) -> dict:
    messages, errors, rows = [], 0, []
    next_ref_id = next_id(reference_rows, "ref_id")

    for _, rrow in df_refs.iterrows():
        ename = str(rrow.get("entity_name", "")).strip()
        citation = clean_val(rrow.get("citation")) or None
        doi = clean_val(rrow.get("publication_DOI")) or None

        eid = _entity_id_for_name(entity_rows, entity_results, site_id, ename)
        if eid is None:
            messages.append((ERR, f"Reference: entity '{ename}' not found"))
            errors += 1
            continue

        existing_ref_id = None
        if doi:
            match = next((r for r in reference_rows if r.get("publication_DOI") == doi), None)
            if match:
                existing_ref_id = int(match["ref_id"])
        if existing_ref_id is None and citation:
            match = next((r for r in reference_rows if r.get("citation") == citation), None)
            if match:
                existing_ref_id = int(match["ref_id"])

        if doi:
            ok, doi_msgs = _validate_doi(doi, citation)
            messages.extend(doi_msgs)
            if not ok:
                errors += 1
        else:
            messages.append((WARN, "No DOI provided for reference -- cannot validate online"))

        if existing_ref_id is not None:
            messages.append((OK, f"Reference already in DB (ref_id={existing_ref_id}) -> will reuse"))
            rows.append({"ref_id": existing_ref_id, "citation": citation, "doi": doi, "entity_id": eid, "action": "reuse"})
        else:
            messages.append((OK, f"Reference is NEW -> INSERT ref_id={next_ref_id}"))
            rows.append({"ref_id": next_ref_id, "citation": citation, "doi": doi, "entity_id": eid, "action": "insert"})
            next_ref_id += 1

    return {"rows": rows, "messages": messages, "errors": errors}


def check_notes(notes_rows, note_texts: list, site_id: int) -> dict:
    """notes.csv has site_id as its PRIMARY KEY -- one row per site, not a
    list. If a note already exists for this site, new text gets appended
    into that single row rather than inserted as a second row."""
    messages = []
    existing = next((r for r in notes_rows if r["site_id"] == str(site_id)), None)
    new_text = "\n\n".join(t for t in note_texts if t)

    if existing:
        if new_text and new_text not in existing["notes"]:
            messages.append((OK, f"Notes: site_id={site_id} already has a note -- new text will be appended to it (not a new row, notes.site_id is a primary key)"))
            return {"action": "append", "combined": existing["notes"] + "\n\n" + new_text, "messages": messages, "errors": 0}
        else:
            messages.append((WARN, f"Notes: site_id={site_id} already has this text (or nothing new to add) -- skipping"))
            return {"action": "none", "combined": None, "messages": messages, "errors": 0}
    else:
        if new_text:
            messages.append((OK, f"Notes: {len(note_texts)} line(s) -> new notes.csv row for site_id={site_id}"))
            return {"action": "insert", "combined": new_text, "messages": messages, "errors": 0}
        return {"action": "none", "combined": None, "messages": messages, "errors": 0}


# -- Person / contact resolution (entity_link_person) ----------------------------
def find_person(person_rows, query):
    q = query.strip().lower()
    return [r for r in person_rows if q in r["name"].lower()]


def resolve_contacts(person_rows, contact_field: str, contact_orcid_field: str, entity_name: str) -> dict:
    """Split a workbook 'contact' cell into name(s), resolve each against
    person.csv (search-or-create, same pattern as add_project.py's
    add_people_loop), and return the list of person_ids to link. Interactive:
    asks for confirmation on ambiguous or missing matches."""
    messages = []
    names = [n.strip() for n in re.split(r"[,;/]", contact_field or "") if n.strip()]
    orcid = clean_val(contact_orcid_field)
    person_ids = []

    if not names:
        messages.append((WARN, f"No contact given for entity '{entity_name}' -- no entity_link_person row(s) will be created"))
        return {"person_ids": [], "messages": messages}

    for name in names:
        matches = find_person(person_rows, name)
        if not matches:
            messages.append((WARN, f"Contact '{name}' not found in person.csv"))
            if prompt_yes_no(f"  Add '{name}' as a brand-new person?", default_yes=True):
                new_id = next_id(person_rows, "person_id")
                use_orcid = orcid if len(names) == 1 else prompt(f"  ORCID for '{name}' (blank if unknown)", required=False)
                new_row = {"person_id": str(new_id), "name": name, "orcid": use_orcid}
                person_rows.append(new_row)
                person_ids.append(str(new_id))
                messages.append((OK, f"  Added new person '{name}' (person_id {new_id})"))
            else:
                messages.append((WARN, f"  Skipped -- '{name}' will not be linked to this entity"))
        elif len(matches) == 1:
            chosen = matches[0]
            person_ids.append(chosen["person_id"])
            messages.append((OK, f"Contact '{name}' matched: {chosen['name']} (person_id {chosen['person_id']})"))
            if orcid and len(names) == 1 and chosen.get("orcid") and chosen["orcid"] != orcid:
                messages.append((WARN, f"  Workbook ORCID '{orcid}' differs from person.csv ORCID '{chosen['orcid']}' for {chosen['name']} -- not auto-changed, review manually"))
        else:
            print(f"\n  '{name}' -- {len(matches)} matches:")
            for i, m in enumerate(matches, 1):
                print(f"    {i}. {m['name']} (person_id {m['person_id']}, ORCID {m['orcid'] or 'unknown'})")
            while True:
                idx = prompt("  Pick a number")
                if idx.isdigit() and 1 <= int(idx) <= len(matches):
                    chosen = matches[int(idx) - 1]
                    person_ids.append(chosen["person_id"])
                    messages.append((OK, f"Contact '{name}' resolved to {chosen['name']} (person_id {chosen['person_id']})"))
                    break
                print("  Invalid choice.")

    return {"person_ids": person_ids, "messages": messages}


# -- Project assignment -----------------------------------------------------------
def find_and_pick_project(projects_rows, query):
    q = query.strip().lower()
    exact_id = [r for r in projects_rows if r["project_id"] == query.strip()]
    matches = exact_id or [r for r in projects_rows if q in (r["project_name"] or "").lower()]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    print(f"  {len(matches)} matches:")
    for i, m in enumerate(matches, 1):
        print(f"    {i}. project_id {m['project_id']} -- {m['project_name']} ({m['project_status']})")
    while True:
        idx = prompt("  Pick a number")
        if idx.isdigit() and 1 <= int(idx) <= len(matches):
            return matches[int(idx) - 1]
        print("  Invalid choice.")


def ask_project_assignment(projects_rows, entity_name: str):
    """Returns a project_id string to link, or None if skipped."""
    if not projects_rows:
        return None
    if not prompt_yes_no(f"Assign entity '{entity_name}' to a project?", default_yes=False):
        return None
    while True:
        query = prompt("  Project (id or partial name, blank to cancel)", required=False)
        if not query:
            return None
        project = find_and_pick_project(projects_rows, query)
        if project is None:
            print(f"  No project matches '{query}'.")
            continue
        print(f"  -> {project['project_name']} (project_id {project['project_id']})")
        return project["project_id"]


# -- Print pre-flight report ------------------------------------------------------
def preflight_report(wb_path, site_result, entity_results, dating_result=None,
                      sample_result=None, lamina_result=None, ref_result=None,
                      notes_result=None):
    print("\n" + "=" * 65)
    print("  SISAL DB UPDATE -- PRE-FLIGHT REPORT  (DRY RUN)")
    print(f"  Workbook : {wb_path.name}")
    print(f"  csv/     : {CSV_DIR}")
    print(f"  Time     : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 65)

    print("\n-- SITE -----------------------------------------------------")
    for fn, msg in site_result["messages"]:
        print(fn(msg) if callable(fn) else msg)

    print("\n-- ENTITIES ---------------------------------------------------")
    for er in entity_results:
        for fn, msg in er["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if dating_result is not None:
        print("\n-- DATING -------------------------------------------------")
        for fn, msg in dating_result["messages"]:
            print(fn(msg) if callable(fn) else msg)
    if sample_result is not None:
        print("\n-- SAMPLE DATA ----------------------------------------------")
        for fn, msg in sample_result["messages"]:
            print(fn(msg) if callable(fn) else msg)
    if lamina_result is not None:
        print("\n-- LAMINA -------------------------------------------------")
        for fn, msg in lamina_result["messages"]:
            print(fn(msg) if callable(fn) else msg)
    if ref_result is not None:
        print("\n-- REFERENCES -----------------------------------------------")
        for fn, msg in ref_result["messages"]:
            print(fn(msg) if callable(fn) else msg)
    if notes_result is not None:
        print("\n-- NOTES ----------------------------------------------------")
        for fn, msg in notes_result["messages"]:
            print(fn(msg) if callable(fn) else msg)

    def _count(result, key="errors"):
        return result[key] if result else 0
    def _warn_count(result):
        return sum(1 for fn, _ in result.get("messages", []) if fn == WARN) if result else 0

    errors = (sum(1 for er in entity_results for fn, _ in er["messages"] if fn == ERR)
              + sum(1 for fn, _ in site_result["messages"] if fn == ERR)
              + _count(dating_result) + _count(sample_result)
              + _count(lamina_result) + _count(ref_result) + _count(notes_result))
    warnings = (sum(1 for er in entity_results for fn, _ in er["messages"] if fn == WARN)
                + sum(1 for fn, _ in site_result["messages"] if fn == WARN)
                + _warn_count(dating_result) + _warn_count(sample_result)
                + _warn_count(lamina_result) + _warn_count(ref_result) + _warn_count(notes_result))

    print("\n-- SUMMARY ----------------------------------------------------")
    print(f"  Site action   : {site_result['action'].upper()} (site_id={site_result['site_id']})")
    inserts = [er for er in entity_results if er["action"] == "insert"]
    skips = [er for er in entity_results if er["action"] == "skip"]
    print(f"  Entities      : {len(inserts)} to insert, {len(skips)} skipped")
    if dating_result is not None:
        print(f"  Dating rows   : {len(dating_result['dating_ids'])} to insert")
    if sample_result is not None:
        print(f"  Sample rows   : {len(sample_result['sample_ids'])} to insert")
    if lamina_result is not None:
        print(f"  Lamina rows   : {len(lamina_result['lamina_ids'])} to insert")
    if ref_result is not None:
        new_refs = sum(1 for r in ref_result["rows"] if r["action"] == "insert")
        print(f"  References    : {new_refs} new, {len(ref_result['rows']) - new_refs} reused")
    if notes_result is not None and notes_result["action"] != "none":
        print(f"  Notes         : {notes_result['action']}")
    print(f"  Warnings      : {warnings}")
    print(f"  Errors        : {errors}")
    if errors:
        print(ERR("Errors found -- fix workbook before running --commit"))
    else:
        print(OK("No blocking errors -- safe to run with --commit"))
    print("=" * 65 + "\n")
    return errors, warnings


def write_preflight_log(wb_path, site_result, entity_results, errors, warnings,
                         dating_result=None, sample_result=None, lamina_result=None,
                         ref_result=None, notes_result=None, committed=False):
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    log_path = LOG_DIR / f"preflight_{stamp}_{wb_path.stem}.md"

    lines = [
        "# SISAL DB Update -- Pre-flight Report", "",
        f"| | |", "|---|---|",
        f"| **Workbook** | `{wb_path.name}` |",
        f"| **Date** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        f"| **Site action** | {site_result['action'].upper()} (site_id={site_result['site_id']}) |",
        f"| **Entities to insert** | {sum(1 for er in entity_results if er['action']=='insert')} |",
        f"| **Errors** | {errors} |",
        f"| **Warnings** | {warnings} |",
        f"| **Status** | {'COMMITTED' if committed else ('DRY RUN' if errors == 0 else 'DRY RUN -- errors found')} |",
        "",
        "## Entities",
        "",
    ]
    for er in entity_results:
        lines.append(f"- `{er.get('_name', '?')}` -> {er['action']} (entity_id={er.get('entity_id')}, persist_id={er.get('persist_id')})")
    lines.append("")
    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def stamp_log_committed(log_path: Path):
    text = log_path.read_text(encoding="utf-8")
    text = text.replace("DRY RUN", "COMMITTED").replace(
        "| **Status** |", f"| **Committed at** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |\n| **Status** |"
    )
    log_path.write_text(text, encoding="utf-8")


# -- Commit -------------------------------------------------------------------
def commit_changes(wb_path, data, site_result, entity_results, dating_result,
                    sample_result, lamina_result, ref_result, notes_result,
                    note_texts, contact_results, project_assignments,
                    loaded):
    site_fields, site_rows = loaded["site"]
    entity_fields, entity_rows = loaded["entity"]
    person_fields, person_rows = loaded["person"]
    entity_link_person_fields, elp_rows = loaded["entity_link_person"]
    notes_fields, notes_rows = loaded["notes"]

    # -- Site --
    if site_result["action"] == "insert":
        row = data["Site metadata"].iloc[0]
        site_rows.append({
            "site_id": str(site_result["site_id"]),
            "site_name": str(row["site_name"]),
            "latitude": str(row["latitude"]),
            "longitude": str(row["longitude"]),
            "elevation": str(row["elevation"]),
            "monitoring": clean_val(row.get("monitoring")),
        })
        print(OK(f"Site '{row['site_name']}' (site_id={site_result['site_id']})"))
    site_id = site_result["site_id"]

    # -- Entities + contacts + project assignment --
    df_entity = data["Entity metadata"]
    for i, er in enumerate(entity_results):
        if er["action"] != "insert":
            print(WARN("Skipping entity (see pre-flight report)"))
            continue
        row = df_entity.iloc[i]
        status_info = str(row.get("entity_status_info", "not applicable")).lower().strip()
        status_notes = str(row.get("entity_status_notes", "")).strip()
        one_and_only = str(row.get("one_and_only", "yes")).lower().strip()

        if one_and_only == "yes" or status_info in ("not applicable", "nan"):
            entity_status = "current"
        elif status_info == "superseded":
            entity_status = "superseded"
        else:
            entity_status = "current"

        insert = {f: "" for f in entity_fields}
        insert.update({
            "site_id": str(site_id),
            "entity_id": str(er["entity_id"]),
            "entity_name": str(row["entity_name"]).strip(),
            "entity_status": entity_status,
            "persist_id": er["persist_id"],
            "depth_ref": "from top",  # v15 workbook always measures depth from top
            # added_in_release_id / last_modified_release_id: left blank --
            # no in-progress database_release row exists yet (see
            # release-management framework.md). Backfill once
            # SISAL_periodic_update_initialiser.py exists and cuts one.
        })
        for col in ENTITY_WORKBOOK_COLS:
            if col in entity_fields:
                insert[col] = clean_val(row.get(col))
        entity_rows.append(insert)
        print(OK(f"Entity '{row['entity_name']}' (entity_id={er['entity_id']})"))

        if status_info == "current" and status_notes:
            for old_id in status_notes.split(";"):
                old_id = old_id.strip()
                if old_id:
                    match = next((r for r in entity_rows if r["entity_id"] == old_id), None)
                    if match:
                        match["entity_status"] = "superseded"
                        match["corresponding_current"] = str(er["entity_id"])
                        print(OK(f"  Set entity_id={old_id} to 'superseded', corresponding_current={er['entity_id']}"))

        # contacts -> entity_link_person
        cr = contact_results.get(er["_name"])
        if cr:
            for pid in cr["person_ids"]:
                if not any(r["entity_id"] == str(er["entity_id"]) and r["person_id"] == pid for r in elp_rows):
                    elp_rows.append({"entity_id": str(er["entity_id"]), "person_id": pid})
            if cr["person_ids"]:
                print(OK(f"  Linked {len(cr['person_ids'])} contact(s) via entity_link_person"))

        # project assignment
        proj_id = project_assignments.get(er["_name"])
        if proj_id:
            ple_fields, ple_rows = loaded["project_link_entity"]
            ple_rows.append({"project_id": proj_id, "entity_id": str(er["entity_id"])})
            print(OK(f"  Assigned to project_id={proj_id}"))

    # -- Dating --
    if "Dating information" in data and dating_result is not None:
        dating_fields, dating_rows = loaded["dating"]
        df_dating = data["Dating information"]
        entity_map = {}
        for name in df_dating["entity_name"].dropna().unique():
            name = str(name).strip()
            eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
            if eid is not None:
                entity_map[name] = eid

        for i, (_, drow) in enumerate(df_dating.iterrows()):
            ename = str(drow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Dating row {i+1}: entity '{ename}' not resolved -- skipping"))
                continue
            insert = {f: "" for f in dating_fields}
            insert["dating_id"] = str(dating_result["dating_ids"][i])
            insert["entity_id"] = str(eid)
            for col in dating_fields:
                if col in insert and insert[col]:
                    continue
                if col in _DATING_WORKBOOK_ONLY or col in _DATING_AGEMODEL_COLS:
                    continue  # leave blank
                if col in df_dating.columns:
                    insert[col] = clean_val(drow.get(col))
            dating_rows.append(insert)
        print(OK(f"Inserted {len(df_dating)} dating rows"))

    # -- Sample data (+ original_chronology, hiatus, gap, proxy tables) --
    if "Sample data" in data and sample_result is not None:
        sample_fields, sample_rows = loaded["sample"]
        oc_fields, oc_rows = loaded["original_chronology"]
        hiatus_fields, hiatus_rows = loaded["hiatus"]
        gap_fields, gap_rows = loaded["gap"]
        df_sample = data["Sample data"]

        entity_map = {}
        for name in df_sample["entity_name"].dropna().unique():
            name = str(name).strip()
            eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
            if eid is not None:
                entity_map[name] = eid

        proxy_inserted = {t: 0 for t in _PROXY_TABLES}
        hiatus_inserted = gap_inserted = 0

        for i, (_, srow) in enumerate(df_sample.iterrows()):
            ename = str(srow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Sample row {i+1}: entity '{ename}' not resolved -- skipping"))
                continue
            sid = sample_result["sample_ids"][i]

            sample_rows.append({
                "sample_id": str(sid), "entity_id": str(eid),
                "sample_thickness": clean_val(srow.get("sample_thickness")),
                "depth_sample": clean_val(srow.get("depth_sample")),
                "mineralogy": clean_val(srow.get("mineralogy")),
                "arag_corr": clean_val(srow.get("arag_corr")),
            })
            oc_rows.append({
                "sample_id": str(sid),
                "interp_age": clean_val(srow.get("interp_age")),
                "interp_age_uncert_pos": clean_val(srow.get("interp_age_uncert_pos")),
                "interp_age_uncert_neg": clean_val(srow.get("interp_age_uncert_neg")),
                "age_model_type": clean_val(srow.get("age_model_type")),
                "ann_lam_check": clean_val(srow.get("ann_lam_check")),
                "dep_rate_check": clean_val(srow.get("dep_rate_check")),
            })

            h = clean_val(srow.get("hiatus"))
            if h:
                hiatus_rows.append({"sample_id": str(sid), "hiatus": h})
                hiatus_inserted += 1
            g = clean_val(srow.get("gap"))
            if g:
                gap_rows.append({"sample_id": str(sid), "gap": g})
                gap_inserted += 1

            for table, (meas_col, prec_col) in _PROXY_TABLES.items():
                meas = clean_val(srow.get(meas_col))
                if not meas:
                    continue
                proxy_fields, proxy_rows = loaded[table]
                proxy_rows.append({"sample_id": str(sid), meas_col: meas, prec_col: clean_val(srow.get(prec_col))})
                proxy_inserted[table] += 1

        summary = f"Inserted {len(df_sample)} sample rows"
        proxy_parts = [f"{t}={c}" for t, c in proxy_inserted.items() if c > 0]
        if proxy_parts:
            summary += f" | proxies: {', '.join(proxy_parts)}"
        if hiatus_inserted:
            summary += f" | hiatus: {hiatus_inserted}"
        if gap_inserted:
            summary += f" | gap: {gap_inserted}"
        print(OK(summary))

    # -- Lamina --
    if "Lamina age vs depth" in data and lamina_result is not None:
        lamina_fields, lamina_rows = loaded["dating_lamina"]
        df_lamina = data["Lamina age vs depth"]
        entity_map = {}
        for name in df_lamina["entity_name"].dropna().unique():
            name = str(name).strip()
            eid = _entity_id_for_name(entity_rows, entity_results, site_id, name)
            if eid is not None:
                entity_map[name] = eid
        for i, (_, lrow) in enumerate(df_lamina.iterrows()):
            ename = str(lrow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Lamina row {i+1}: entity '{ename}' not resolved -- skipping"))
                continue
            lamina_rows.append({
                "dating_lamina_id": str(lamina_result["lamina_ids"][i]), "entity_id": str(eid),
                "depth_lam": clean_val(lrow.get("depth_lam")),
                "lam_thickness": clean_val(lrow.get("lam_thickness")),
                "lam_age": clean_val(lrow.get("lam_age")),
                "lam_age_uncert_pos": clean_val(lrow.get("lam_age_uncert_pos")),
                "lam_age_uncert_neg": clean_val(lrow.get("lam_age_uncert_neg")),
            })
        print(OK(f"Inserted {len(df_lamina)} lamina rows"))

    # -- References --
    if ref_result is not None:
        reference_fields, reference_rows = loaded["reference"]
        elr_fields, elr_rows = loaded["entity_link_reference"]
        n_inserted = n_linked = 0
        for r in ref_result["rows"]:
            if r["action"] == "insert":
                reference_rows.append({"ref_id": str(r["ref_id"]), "citation": r["citation"] or "", "publication_DOI": r["doi"] or ""})
                n_inserted += 1
            if not any(x["entity_id"] == str(r["entity_id"]) and x["ref_id"] == str(r["ref_id"]) for x in elr_rows):
                elr_rows.append({"entity_id": str(r["entity_id"]), "ref_id": str(r["ref_id"])})
                n_linked += 1
        print(OK(f"References: {n_inserted} inserted, {n_linked} entity links added"))

    # -- Notes --
    if notes_result is not None and notes_result["action"] != "none":
        if notes_result["action"] == "insert":
            notes_rows.append({"site_id": str(site_id), "notes": notes_result["combined"]})
        elif notes_result["action"] == "append":
            match = next(r for r in notes_rows if r["site_id"] == str(site_id))
            match["notes"] = notes_result["combined"]
        print(OK(f"Notes: {notes_result['action']} for site_id={site_id}"))

    # -- Write every touched CSV --
    written = []
    for table, (fields, rows) in loaded.items():
        use_bom = table in ("person", "projects")
        write_csv(CSV_DIR / f"{table}.csv", fields, rows, use_bom=use_bom)
        written.append(table)
    print(OK(f"Wrote {len(written)} CSV file(s): {', '.join(sorted(written))}"))


# -- Main -----------------------------------------------------------------------
_ALL_TABLES = [
    "site", "entity", "person", "entity_link_person", "projects", "project_link_entity",
    "dating", "sample", "original_chronology", "dating_lamina", "hiatus", "gap",
    "reference", "entity_link_reference", "notes",
    "d18O", "d13C", "Sr_Ca", "Mg_Ca", "Ba_Ca", "U_Ca", "P_Ca", "Sr_isotopes",
]


def main():
    parser = argparse.ArgumentParser(description="SISAL DB update -- add entity (CSV-first, v3.1)")
    parser.add_argument("--workbook", required=True, help="Path to QC-passed workbook (.xlsx)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                       help="Run checks only, write nothing (default)")
    mode.add_argument("--commit", dest="dry_run", action="store_false",
                       help="Write to csv/ after passing checks")
    args = parser.parse_args()

    wb_path = Path(args.workbook)
    if not wb_path.exists():
        print(ERR(f"Workbook not found: {wb_path}"))
        sys.exit(1)

    print("=== SISAL entity addition ===")
    if not prompt_yes_no(
        "\nHas this workbook already passed Auto-QC (wb_check_v15.py) with 0 errors?",
        default_yes=False,
    ):
        print(ERR("Refusing to proceed -- run the workbook through Auto-QC first."))
        sys.exit(1)

    print(f"\nReading workbook: {wb_path.name} ...")
    data = read_workbook(wb_path)
    print(f"  Sheets loaded: {list(data.keys())}")

    enums, tables = parse_dbml(SCHEMA_DBML)

    loaded = load_all(_ALL_TABLES)
    site_fields, site_rows = loaded["site"]
    entity_fields, entity_rows = loaded["entity"]
    person_fields, person_rows = loaded["person"]
    projects_fields, projects_rows = loaded["projects"]
    dating_fields, dating_rows = loaded["dating"]
    sample_fields, sample_rows = loaded["sample"]
    lamina_fields, lamina_rows = loaded["dating_lamina"]
    reference_fields, reference_rows = loaded["reference"]
    notes_fields, notes_rows = loaded["notes"]

    if "Site metadata" not in data:
        print(ERR("No 'Site metadata' sheet found or it is empty."))
        sys.exit(1)
    site_row = data["Site metadata"].iloc[0]
    site_result = check_site(site_rows, site_row, tables, enums)

    if "Entity metadata" not in data:
        print(ERR("No 'Entity metadata' sheet found or it is empty."))
        sys.exit(1)
    entity_results = []
    for _, erow in data["Entity metadata"].iterrows():
        if pd.isna(erow.get("entity_name")):
            continue
        entity_results.append(check_entity(entity_rows, erow, site_result["site_id"], tables, enums))

    dating_result = None
    if "Dating information" in data:
        dating_result = check_dating(dating_rows, data["Dating information"], site_result["site_id"], entity_rows, entity_results)

    sample_result = None
    if "Sample data" in data:
        sample_result = check_sample(sample_rows, data["Sample data"], site_result["site_id"], entity_rows, entity_results)

    lamina_result = None
    if "Lamina age vs depth" in data and not data["Lamina age vs depth"].empty:
        lamina_result = check_lamina(lamina_rows, data["Lamina age vs depth"], site_result["site_id"], entity_rows, entity_results)

    ref_result = None
    if "References" in data and not data["References"].empty:
        ref_result = check_references(reference_rows, data["References"], entity_rows, entity_results, site_result["site_id"])

    notes_result = None
    note_texts = []
    xls = pd.ExcelFile(wb_path)
    if "Notes" in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name="Notes", header=None)
        note_texts = [str(v).strip() for v in raw.iloc[:, 0].dropna() if str(v).strip()]
        if note_texts:
            notes_result = check_notes(notes_rows, note_texts, site_result["site_id"])

    errors, warnings = preflight_report(
        wb_path, site_result, entity_results, dating_result, sample_result,
        lamina_result, ref_result, notes_result,
    )
    log_path = write_preflight_log(
        wb_path, site_result, entity_results, errors, warnings,
        dating_result, sample_result, lamina_result, ref_result, notes_result,
    )
    print(INFO(f"Pre-flight log written -> {log_path}"))

    if args.dry_run:
        print("Dry run complete. Re-run with --commit to apply changes.\n")
        return

    if errors:
        print(ERR("Aborting --commit due to errors in pre-flight report."))
        sys.exit(1)

    # Interactive steps only run at commit time (dry-run stays fully non-interactive
    # except for the Auto-QC gate, so it can be reviewed without committing to any
    # person/project decisions yet).
    print("\n--- Resolving contacts for each new entity ---")
    df_entity = data["Entity metadata"]
    contact_results = {}
    for i, er in enumerate(entity_results):
        if er["action"] != "insert":
            continue
        row = df_entity.iloc[i]
        print(f"\nEntity: {er['_name']}")
        cr = resolve_contacts(person_rows, str(row.get("contact", "")), row.get("contact_orcid"), er["_name"])
        for fn, msg in cr["messages"]:
            print(fn(msg) if callable(fn) else msg)
        contact_results[er["_name"]] = cr

    print("\n--- Project assignment ---")
    project_assignments = {}
    for er in entity_results:
        if er["action"] != "insert":
            continue
        proj_id = ask_project_assignment(projects_rows, er["_name"])
        if proj_id:
            project_assignments[er["_name"]] = proj_id

    print("\n=== Final confirmation ===")
    if not prompt_yes_no("Write all changes to csv/?", default_yes=True):
        print("Aborted -- nothing written.")
        return

    commit_changes(
        wb_path, data, site_result, entity_results, dating_result, sample_result,
        lamina_result, ref_result, notes_result, note_texts, contact_results,
        project_assignments, loaded,
    )
    stamp_log_committed(log_path)
    print(INFO(f"Log updated with commit timestamp -> {log_path}"))
    print("\nNext: rebuild and verify before committing to git:")
    print("  python3 USER_scripts/build_db.py /tmp/sisal_check")


if __name__ == "__main__":
    main()
