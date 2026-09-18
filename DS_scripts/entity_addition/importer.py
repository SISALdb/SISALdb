"""
SISAL DB update importer — site, entity, dating, and sample data.

Usage:
    python importer.py --workbook <path.xlsx> [--dry-run] [--commit]

Flags:
    --dry-run   (default) Run all checks and print a pre-flight report.
                Nothing is written to DB or CSVs.
    --commit    After a clean dry-run, write to DB and update CSVs.

Workflow:
    1. Always run --dry-run first and review the report.
    2. If report looks good, re-run with --commit.
"""

import argparse
import re
import sqlite3
import sys
import csv
import shutil
import urllib.request
import urllib.error
import urllib.parse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from config import DB_PATH, CSV_DIR, ENTITY_WORKBOOK_COLS, ENTITY_WORKBOOK_ONLY

# Log directory — one markdown file per import run, committed with each DB release
LOG_DIR = Path(__file__).parent / "logs"

# Dating columns present in workbook but not in DB (skip on insert)
_DATING_WORKBOOK_ONLY = {"entity_name", "chem_year"}

# DB columns filled by the age-model pipeline, not the workbook (insert as NULL)
_DATING_AGEMODEL_COLS = {
    "date_used_lin_interp", "date_used_lin_reg", "date_used_Bchron",
    "date_used_Bacon", "date_used_OxCal", "date_used_copRa", "date_used_StalAge",
}

# Sample sheet columns that go into `sample` table (besides entity_id, sample_id)
_SAMPLE_COLS = {"depth_sample", "mineralogy", "arag_corr", "sample_thickness"}

# Sample sheet columns that go into `original_chronology`
_ORIG_CHRON_COLS = {
    "interp_age", "interp_age_uncert_pos", "interp_age_uncert_neg",
    "age_model_type", "ann_lam_check", "dep_rate_check",
}

# Proxy table → (measurement col, precision col)
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

# Entity columns shown in the preflight log data table (most informative subset)
_LOG_ENTITY_PREVIEW_COLS = [
    "entity_name", "speleothem_type", "d13C", "d18O",
    "mineralogy_petrology_fabric", "Sr_Ca", "Mg_Ca", "Ba_Ca",
    "contact", "data_DOI_URL",
]


# ── Colour helpers (terminal output) ──────────────────────────────────────────
OK   = lambda s: f"\033[32m✅  {s}\033[0m"
WARN = lambda s: f"\033[33m⚠️   {s}\033[0m"
ERR  = lambda s: f"\033[31m❌  {s}\033[0m"
INFO = lambda s: f"    {s}"


# ── Workbook reader ────────────────────────────────────────────────────────────
def read_workbook(wb_path: Path) -> dict[str, pd.DataFrame]:
    """Read Site metadata and Entity metadata sheets; row 2 is the header."""
    drop_sheets = {
        "INSTRUCTIONS", "SISALv3_database_structure", "Entity in SISAL ",
        "Notes", "Drop-down Lists",   # Notes is read separately (no header row)
    }
    xls = pd.ExcelFile(wb_path)
    data = {}
    for sheet in xls.sheet_names:
        if sheet in drop_sheets:
            continue
        df = pd.read_excel(xls, sheet_name=sheet, header=1)  # row index 1 = row 2
        # Drop columns that are entirely None (trailing empty cols)
        df = df.loc[:, df.columns.notna()]
        # Drop rows that are entirely empty
        df = df.dropna(how="all").reset_index(drop=True)
        if not df.empty:
            data[sheet] = df
    return data


# ── ID helpers ─────────────────────────────────────────────────────────────────
def next_id(cur: sqlite3.Cursor, table: str, id_col: str) -> int:
    cur.execute(f"SELECT MAX(CAST({id_col} AS INTEGER)) FROM {table}")
    row = cur.fetchone()
    return (row[0] or 0) + 1


def normalise(s: str) -> str:
    """Lowercase, strip spaces — used for fuzzy name matching."""
    return re.sub(r"\s+", "", str(s).lower().strip())


# Values from the workbook that should be stored as NULL
_NULL_SENTINELS = {"none-not applicable", "nan", "none", "n/a", ""}

def clean_val(val) -> str | None:
    """Normalise a workbook cell value for DB insert.

    Returns None (→ NULL/NA) for missing or placeholder values,
    otherwise returns a stripped string.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if s.lower() in _NULL_SENTINELS:
        return None
    return s


# ── Pre-flight checks ──────────────────────────────────────────────────────────
def check_site(cur: sqlite3.Cursor, site_row: pd.Series) -> dict:
    """
    Returns a result dict:
      action  : 'insert' | 'reuse'
      site_id : int (existing if reuse, proposed new if insert)
      messages: list of (level, text)  level in OK/WARN/ERR
    """
    name = site_row["site_name"]
    messages = []

    cur.execute(
        "SELECT site_id, site_name, latitude, longitude, elevation "
        "FROM site WHERE REPLACE(LOWER(site_name),' ','') = ?",
        (normalise(name),),
    )
    existing = cur.fetchone()

    if existing:
        sid, ename, elat, elon, eelev = existing
        messages.append((OK, f"Site '{name}' already in SISAL (site_id={sid}) → will reuse, no INSERT"))
        # Warn if coordinates differ noticeably
        try:
            if abs(float(elat) - float(site_row["latitude"])) > 0.01:
                messages.append((WARN, f"  Latitude mismatch: DB={elat}, workbook={site_row['latitude']}"))
            if abs(float(elon) - float(site_row["longitude"])) > 0.01:
                messages.append((WARN, f"  Longitude mismatch: DB={elon}, workbook={site_row['longitude']}"))
        except (TypeError, ValueError):
            pass
        return {"action": "reuse", "site_id": int(sid), "messages": messages}
    else:
        new_id = next_id(cur, "site", "site_id")
        messages.append((OK, f"Site '{name}' is NEW → will INSERT with site_id={new_id}"))
        return {"action": "insert", "site_id": new_id, "messages": messages}


def check_entity(cur: sqlite3.Cursor, entity_row: pd.Series, site_id: int) -> dict:
    """
    Returns a result dict:
      action         : 'insert' | 'skip' | 'update_status'
      entity_id      : int (proposed new, or existing if skip)
      persist_id     : str
      messages       : list of (level, text)
    """
    name = str(entity_row["entity_name"]).strip()
    one_and_only = str(entity_row.get("one_and_only", "yes")).lower().strip()
    status_info = str(entity_row.get("entity_status_info", "not applicable")).lower().strip()
    status_notes = str(entity_row.get("entity_status_notes", "")).strip()
    messages = []

    # Build persist_id: remove trailing year, remove dashes, uppercase
    clean = re.sub(r"[-_]\d{4}$", "", name)
    persist_id = f"{site_id}-{re.sub(r'[-]', '', clean).upper()}"

    # Check if entity name already exists at this site
    cur.execute(
        "SELECT entity_id, entity_name, entity_status FROM entity "
        "WHERE site_id=? AND REPLACE(LOWER(entity_name),' ','')=?",
        (str(site_id), normalise(name)),
    )
    existing_same_name = cur.fetchall()

    # Check if persist_id already exists
    cur.execute("SELECT entity_id FROM entity WHERE persist_id=?", (persist_id,))
    existing_persist = cur.fetchone()

    if existing_same_name:
        ids = ", ".join(str(r[0]) for r in existing_same_name)
        statuses = ", ".join(r[2] for r in existing_same_name)
        messages.append((WARN,
            f"Entity '{name}' already exists at site_id={site_id} "
            f"(entity_id={ids}, status={statuses})"))
        messages.append((ERR,
            f"  → Cannot insert duplicate. Check 'one_and_only' and 'entity_status_info' "
            f"in workbook. Skipping this entity."))
        return {"action": "skip", "entity_id": int(existing_same_name[0][0]),
                "persist_id": persist_id, "_name": name, "messages": messages}

    if existing_persist:
        messages.append((WARN,
            f"persist_id '{persist_id}' already exists (entity_id={existing_persist[0]}) "
            f"— will still insert entity with this persist_id (allowed for superseded chains)"))

    new_id = next_id(cur, "entity", "entity_id")

    if one_and_only == "yes":
        messages.append((OK,
            f"Entity '{name}' is NEW, one_and_only=yes → INSERT as 'current' "
            f"(entity_id={new_id}, persist_id={persist_id})"))
        action = "insert"
    elif status_info == "current":
        messages.append((OK,
            f"Entity '{name}' is NEW, supersedes entity_id(s): {status_notes} "
            f"→ INSERT as 'current', those IDs will be set to 'superseded'"))
        if status_notes:
            for old_id in status_notes.split(";"):
                old_id = old_id.strip()
                cur.execute("SELECT entity_name, entity_status FROM entity WHERE entity_id=?", (old_id,))
                r = cur.fetchone()
                if r:
                    messages.append((INFO, f"  Will supersede entity_id={old_id} ('{r[0]}', currently '{r[1]}')"))
                else:
                    messages.append((WARN, f"  entity_id={old_id} listed in status_notes but NOT found in DB"))
        action = "insert"
    else:
        messages.append((OK,
            f"Entity '{name}' is NEW (status_info='{status_info}') "
            f"→ INSERT (entity_id={new_id}, persist_id={persist_id})"))
        action = "insert"

    return {"action": action, "entity_id": new_id, "persist_id": persist_id,
            "_name": name, "messages": messages}


# ── Dating checks ──────────────────────────────────────────────────────────────
def check_dating(
    cur: sqlite3.Cursor,
    df_dating: pd.DataFrame,
    site_id: int,
    entity_results: list[dict],
) -> dict:
    """
    Resolve entity_name → entity_id for each dating row and assign dating_ids.
    Content validation is handled upstream by Jens's U-Th check script.

    Returns:
      {
        entity_map : {entity_name: entity_id}
        dating_ids : [int, ...]
        messages   : [(level_fn, text), ...]
        errors     : int
      }
    """
    messages = []
    errors = 0
    entity_map: dict[str, int] = {}

    for name in df_dating["entity_name"].dropna().unique():
        name = str(name).strip()
        # Check DB first (existing entities)
        cur.execute(
            "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
            (str(site_id), name),
        )
        row = cur.fetchone()
        if row:
            entity_map[name] = int(row[0])
        else:
            # Check pending inserts in this run
            matched = [er for er in entity_results
                       if er["action"] == "insert" and er.get("_name") == name]
            if matched:
                entity_map[name] = matched[0]["entity_id"]
            else:
                messages.append((ERR,
                    f"Dating: entity '{name}' not found in DB and not pending insert "
                    f"— cannot assign entity_id"))
                errors += 1

    cur.execute("SELECT MAX(CAST(dating_id AS INTEGER)) FROM dating")
    max_id = cur.fetchone()[0] or 0
    dating_ids = list(range(max_id + 1, max_id + 1 + len(df_dating)))

    messages.append((OK,
        f"Dating: {len(df_dating)} rows, entity_id(s) {sorted(entity_map.values())} "
        f"→ dating_id {dating_ids[0]}–{dating_ids[-1]}"))

    return {
        "entity_map": entity_map,
        "dating_ids": dating_ids,
        "messages":   messages,
        "errors":     errors,
    }


# ── Sample checks ─────────────────────────────────────────────────────────────
def check_sample(
    cur: sqlite3.Cursor,
    df_sample: pd.DataFrame,
    site_id: int,
    entity_results: list[dict],
) -> dict:
    """
    Resolve entity_name → entity_id and assign sample_ids.
    Counts rows per proxy table for the preflight summary.
    Content validation is handled upstream.

    Returns:
      {
        entity_map  : {entity_name: entity_id}
        sample_ids  : [int, ...]
        proxy_counts: {table_name: int}
        hiatus_count: int
        gap_count   : int
        messages    : [(level_fn, text), ...]
        errors      : int
      }
    """
    messages = []
    errors = 0
    entity_map: dict[str, int] = {}

    for name in df_sample["entity_name"].dropna().unique():
        name = str(name).strip()
        cur.execute(
            "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
            (str(site_id), name),
        )
        row = cur.fetchone()
        if row:
            entity_map[name] = int(row[0])
        else:
            matched = [er for er in entity_results
                       if er["action"] == "insert" and er.get("_name") == name]
            if matched:
                entity_map[name] = matched[0]["entity_id"]
            else:
                messages.append((ERR,
                    f"Sample: entity '{name}' not found in DB and not pending insert"))
                errors += 1

    # Assign sample_ids sequentially
    cur.execute("SELECT MAX(CAST(sample_id AS INTEGER)) FROM sample")
    max_id = cur.fetchone()[0] or 0
    n = len(df_sample)
    sample_ids = list(range(max_id + 1, max_id + 1 + n))

    # Count proxy rows (only where measurement is non-null)
    proxy_counts = {}
    for table, (meas_col, _) in _PROXY_TABLES.items():
        if meas_col in df_sample.columns:
            proxy_counts[table] = int(df_sample[meas_col].notna().sum())

    hiatus_count = int(df_sample["hiatus"].notna().sum()) if "hiatus" in df_sample.columns else 0
    gap_count    = int(df_sample["gap"].notna().sum())    if "gap"    in df_sample.columns else 0

    messages.append((OK,
        f"Sample: {n} rows → sample_id {sample_ids[0]}–{sample_ids[-1]}"))
    proxy_summary = ", ".join(f"{t}={c}" for t, c in proxy_counts.items() if c > 0)
    if proxy_summary:
        messages.append((OK, f"  Proxy rows: {proxy_summary}"))
    if hiatus_count:
        messages.append((OK, f"  Hiatus rows: {hiatus_count}"))
    if gap_count:
        messages.append((OK, f"  Gap rows: {gap_count}"))

    return {
        "entity_map":   entity_map,
        "sample_ids":   sample_ids,
        "proxy_counts": proxy_counts,
        "hiatus_count": hiatus_count,
        "gap_count":    gap_count,
        "messages":     messages,
        "errors":       errors,
    }


# ── Lamina checks ─────────────────────────────────────────────────────────────
def check_lamina(
    cur: sqlite3.Cursor,
    df_lamina: pd.DataFrame,
    site_id: int,
    entity_results: list[dict],
) -> dict:
    """Resolve entity_name → entity_id and assign dating_lamina_ids."""
    messages = []
    errors = 0
    entity_map: dict[str, int] = {}

    for name in df_lamina["entity_name"].dropna().unique():
        name = str(name).strip()
        cur.execute(
            "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
            (str(site_id), name),
        )
        row = cur.fetchone()
        if row:
            entity_map[name] = int(row[0])
        else:
            matched = [er for er in entity_results
                       if er["action"] == "insert" and er.get("_name") == name]
            if matched:
                entity_map[name] = matched[0]["entity_id"]
            else:
                messages.append((ERR,
                    f"Lamina: entity '{name}' not found in DB and not pending insert"))
                errors += 1

    cur.execute("SELECT MAX(CAST(dating_lamina_id AS INTEGER)) FROM dating_lamina")
    max_id = cur.fetchone()[0] or 0
    n = len(df_lamina)
    lamina_ids = list(range(max_id + 1, max_id + 1 + n))

    messages.append((OK,
        f"Lamina: {n} rows, entity_id(s) {sorted(entity_map.values())} "
        f"→ dating_lamina_id {lamina_ids[0]}–{lamina_ids[-1]}"))

    return {
        "entity_map": entity_map,
        "lamina_ids": lamina_ids,
        "messages":   messages,
        "errors":     errors,
    }


# ── DOI validation via CrossRef ───────────────────────────────────────────────
def _validate_doi(doi: str, citation: str | None) -> tuple[bool, list[tuple]]:
    """
    Query CrossRef for the DOI and cross-check against the citation string.

    Returns:
        (ok: bool, messages: [(level_fn, text), ...])
          ok=True  → DOI resolves and title/author match looks plausible
          ok=False → DOI doesn't exist (hard error); network failure → ok=True with WARN
    """
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
        msgs.append((WARN, f"CrossRef returned HTTP {e.code} for {doi} — cannot validate"))
        return True, msgs
    except Exception as e:
        msgs.append((WARN, f"CrossRef lookup failed ({type(e).__name__}) — skipping DOI validation"))
        return True, msgs

    work = payload.get("message", {})

    # Title check
    titles = work.get("title", [])
    cr_title = titles[0] if titles else ""
    if cr_title and citation:
        # Simple substring check on key words (first 6 words of CrossRef title)
        key_words = re.sub(r"[^\w\s]", "", cr_title.lower()).split()[:6]
        citation_lower = citation.lower()
        matched = sum(1 for w in key_words if w in citation_lower)
        if matched < max(2, len(key_words) // 2):
            msgs.append((WARN,
                f"DOI resolves but title may not match citation.\n"
                f"    CrossRef title : '{cr_title[:80]}'\n"
                f"    Citation snippet: '{(citation or '')[:80]}'"))
        else:
            msgs.append((OK, f"DOI validated ✓  CrossRef title: '{cr_title[:80]}'"))
    else:
        msgs.append((OK, f"DOI resolves (no title to cross-check): {doi}"))

    # Author check — just first author family name
    authors = work.get("author", [])
    if authors and citation:
        first_family = authors[0].get("family", "")
        if first_family and first_family.lower() not in citation.lower():
            msgs.append((WARN,
                f"First author '{first_family}' from CrossRef not found in citation text"))

    return True, msgs


# ── Reference checks ───────────────────────────────────────────────────────────
def check_references(
    cur: sqlite3.Cursor,
    df_refs: pd.DataFrame,
    entity_results: list[dict],
    site_id: int,
) -> dict:
    """
    For each reference row: check if it already exists (by DOI, then by citation),
    assign new ref_ids where needed, and resolve entity_name → entity_id.

    Returns:
      {
        rows      : [{"ref_id", "citation", "doi", "entity_id", "action": "insert"|"reuse"}, ...]
        messages  : [(level_fn, text), ...]
        errors    : int
      }
    """
    messages = []
    errors = 0
    rows = []

    cur.execute("SELECT MAX(CAST(ref_id AS INTEGER)) FROM reference")
    next_ref_id = (cur.fetchone()[0] or 0) + 1

    for _, rrow in df_refs.iterrows():
        ename    = str(rrow.get("entity_name", "")).strip()
        citation = clean_val(rrow.get("citation"))
        doi      = clean_val(rrow.get("publication_DOI"))

        # Resolve entity_id
        cur.execute(
            "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
            (str(site_id), ename),
        )
        db_row = cur.fetchone()
        if db_row:
            eid = int(db_row[0])
        else:
            matched = [er for er in entity_results
                       if er["action"] == "insert" and er.get("_name") == ename]
            if matched:
                eid = matched[0]["entity_id"]
            else:
                messages.append((ERR, f"Reference: entity '{ename}' not found"))
                errors += 1
                continue

        # Check if reference already exists
        existing_ref_id = None
        if doi:
            cur.execute(
                "SELECT ref_id FROM reference WHERE publication_DOI=?", (doi,)
            )
            hit = cur.fetchone()
            if hit:
                existing_ref_id = int(hit[0])
        if existing_ref_id is None and citation:
            cur.execute(
                "SELECT ref_id FROM reference WHERE citation=?", (citation,)
            )
            hit = cur.fetchone()
            if hit:
                existing_ref_id = int(hit[0])

        # DOI validation via CrossRef
        if doi:
            ok, doi_msgs = _validate_doi(doi, citation)
            messages.extend(doi_msgs)
            if not ok:
                errors += 1
        else:
            messages.append((WARN, f"No DOI provided for reference — cannot validate online"))

        if existing_ref_id is not None:
            messages.append((OK,
                f"Reference already in DB (ref_id={existing_ref_id}) → will reuse"))
            rows.append({"ref_id": existing_ref_id, "citation": citation,
                         "doi": doi, "entity_id": eid, "action": "reuse"})
        else:
            messages.append((OK,
                f"Reference is NEW → INSERT ref_id={next_ref_id}"))
            rows.append({"ref_id": next_ref_id, "citation": citation,
                         "doi": doi, "entity_id": eid, "action": "insert"})
            next_ref_id += 1

    return {"rows": rows, "messages": messages, "errors": errors}


# ── Notes checks ───────────────────────────────────────────────────────────────
def check_notes(
    cur: sqlite3.Cursor,
    note_texts: list[str],
    site_id: int,
) -> dict:
    """
    Check each note against existing notes for this site.

    Returns:
      {
        to_insert : [str, ...]   (new notes only)
        messages  : [(level_fn, text), ...]
        errors    : int
      }
    """
    messages = []
    to_insert = []

    cur.execute("SELECT notes FROM notes WHERE site_id=?", (str(site_id),))
    existing = {r[0] for r in cur.fetchall()}

    for text in note_texts:
        if text in existing:
            messages.append((WARN, f"Note already exists for site_id={site_id}, skipping: '{text[:60]}…'"))
        else:
            to_insert.append(text)

    if to_insert:
        messages.append((OK, f"Notes: {len(to_insert)} new note(s) to insert for site_id={site_id}"))

    return {"to_insert": to_insert, "messages": messages, "errors": 0}


# ── Print pre-flight report ────────────────────────────────────────────────────
def preflight_report(
    wb_path: Path,
    site_result: dict,
    entity_results: list[dict],
    dating_result: dict | None = None,
    sample_result: dict | None = None,
    lamina_result: dict | None = None,
    ref_result: dict | None = None,
    notes_result: dict | None = None,
):
    print("\n" + "═" * 65)
    print(f"  SISAL DB UPDATE — PRE-FLIGHT REPORT  (DRY RUN)")
    print(f"  Workbook : {wb_path.name}")
    print(f"  DB       : {DB_PATH}")
    print(f"  Time     : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 65)

    print("\n── SITE ──────────────────────────────────────────────────────")
    for fn, msg in site_result["messages"]:
        print(fn(msg) if callable(fn) else msg)

    print("\n── ENTITIES ──────────────────────────────────────────────────")
    for er in entity_results:
        for fn, msg in er["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if dating_result is not None:
        print("\n── DATING ────────────────────────────────────────────────────")
        for fn, msg in dating_result["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if sample_result is not None:
        print("\n── SAMPLE DATA ───────────────────────────────────────────────")
        for fn, msg in sample_result["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if lamina_result is not None:
        print("\n── LAMINA ────────────────────────────────────────────────────")
        for fn, msg in lamina_result["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if ref_result is not None:
        print("\n── REFERENCES ────────────────────────────────────────────────")
        for fn, msg in ref_result["messages"]:
            print(fn(msg) if callable(fn) else msg)

    if notes_result is not None:
        print("\n── NOTES ─────────────────────────────────────────────────────")
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
                + _warn_count(lamina_result) + _warn_count(ref_result)
                + _warn_count(notes_result))

    print("\n── SUMMARY ───────────────────────────────────────────────────")
    print(f"  Site action   : {site_result['action'].upper()} (site_id={site_result['site_id']})")
    inserts = [er for er in entity_results if er["action"] == "insert"]
    skips   = [er for er in entity_results if er["action"] == "skip"]
    print(f"  Entities      : {len(inserts)} to insert, {len(skips)} skipped")
    if dating_result is not None:
        print(f"  Dating rows   : {len(dating_result['dating_ids'])} to insert")
    if sample_result is not None:
        print(f"  Sample rows   : {len(sample_result['sample_ids'])} to insert")
    if lamina_result is not None:
        print(f"  Lamina rows   : {len(lamina_result['lamina_ids'])} to insert")
    if ref_result is not None:
        new_refs = sum(1 for r in ref_result["rows"] if r["action"] == "insert")
        print(f"  References    : {new_refs} new, {len(ref_result['rows'])-new_refs} reused")
    if notes_result is not None:
        print(f"  Notes         : {len(notes_result['to_insert'])} to insert")
    print(f"  Warnings      : {warnings}")
    print(f"  Errors        : {errors}")
    if errors:
        print(ERR("Errors found — fix workbook before running --commit"))
    else:
        print(OK("No blocking errors — safe to run with --commit"))
    print("═" * 65 + "\n")
    return errors, warnings


# ── Preflight log (markdown, saved to logs/) ───────────────────────────────────
def write_preflight_log(
    wb_path: Path,
    data: dict,
    site_result: dict,
    entity_results: list[dict],
    errors: int,
    warnings: int,
    dating_result: dict | None = None,
    sample_result: dict | None = None,
    lamina_result: dict | None = None,
    ref_result: dict | None = None,
    notes_result: dict | None = None,
) -> Path:
    """Write a human-readable markdown log to logs/ and return the path."""
    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    log_path = LOG_DIR / f"preflight_{stamp}_{wb_path.stem}.md"

    lines = []
    a = lines.append

    a(f"# SISAL DB Update — Pre-flight Log")
    a(f"")
    a(f"| | |")
    a(f"|---|---|")
    a(f"| **Workbook** | `{wb_path.name}` |")
    a(f"| **DB** | `{DB_PATH.name}` |")
    a(f"| **DB path** | `{DB_PATH}` |")
    a(f"| **Time** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |")
    a(f"| **Errors** | {errors} |")
    a(f"| **Warnings** | {warnings} |")
    outcome = "❌ Errors found — not committed" if errors else "⏳ Dry-run only — pending commit decision"
    a(f"| **Outcome** | {outcome} |")
    a(f"")

    # ── Site section ──────────────────────────────────────────────────────────
    a(f"## Site")
    a(f"")
    df_site = data.get("Site metadata", pd.DataFrame())
    if not df_site.empty:
        row = df_site.iloc[0]
        site_cols = ["site_name", "latitude", "longitude", "elevation",
                     "country", "rock_type", "monitoring"]
        a(f"| Field | Workbook value | DB action |")
        a(f"|---|---|---|")
        action_str = (
            f"REUSE existing site_id={site_result['site_id']}"
            if site_result["action"] == "reuse"
            else f"INSERT new site_id={site_result['site_id']}"
        )
        for col in site_cols:
            val = row.get(col, "")
            if pd.isna(val):
                val = ""
            elif col in ("latitude", "longitude"):
                try:
                    val = str(round(float(val), 4))
                except (TypeError, ValueError):
                    val = str(val)
            else:
                val = str(val)
            db_note = action_str if col == "site_name" else ("↑ from workbook" if site_result["action"] == "insert" else "—")
            a(f"| {col} | {val} | {db_note} |")
    a(f"")

    # ── Coordinate / metadata warnings ────────────────────────────────────────
    site_warns = [msg for fn, msg in site_result["messages"] if fn == WARN]
    site_errs  = [msg for fn, msg in site_result["messages"] if fn == ERR]
    if site_warns or site_errs:
        a(f"### Site warnings / errors")
        for m in site_warns:
            a(f"- ⚠️ {m.strip()}")
        for m in site_errs:
            a(f"- ❌ {m.strip()}")
        a(f"")

    # ── Entity section ────────────────────────────────────────────────────────
    a(f"## Entities")
    a(f"")
    inserts = [er for er in entity_results if er["action"] == "insert"]
    skips   = [er for er in entity_results if er["action"] == "skip"]
    a(f"- **To insert:** {len(inserts)}")
    a(f"- **Skipped (duplicate/error):** {len(skips)}")
    a(f"")

    df_entity = data.get("Entity metadata", pd.DataFrame())
    if not df_entity.empty and entity_results:
        # Build a preview table: proposed IDs + key metadata columns
        preview_cols = [c for c in _LOG_ENTITY_PREVIEW_COLS if c in df_entity.columns]
        header = ["entity_id", "persist_id", "action", "entity_status"] + preview_cols
        a("| " + " | ".join(header) + " |")
        a("| " + " | ".join(["---"] * len(header)) + " |")

        for i, er in enumerate(entity_results):
            if i >= len(df_entity):
                break
            row = df_entity.iloc[i]
            status = (
                "current" if str(row.get("one_and_only", "")).lower() == "yes"
                else str(row.get("entity_status_info", "")).lower()
            )
            cells = [
                str(er["entity_id"]),
                er["persist_id"],
                er["action"].upper(),
                status,
            ]
            for col in preview_cols:
                val = clean_val(row.get(col, ""))
                val = "NA" if val is None else val
                # Truncate long URLs/notes
                val = (val[:60] + "…") if len(val) > 63 else val
                cells.append(val)
            a("| " + " | ".join(cells) + " |")
        a(f"")

        # Entity-level warnings and errors
        entity_issues = []
        for i, er in enumerate(entity_results):
            for fn, msg in er["messages"]:
                if fn in (WARN, ERR):
                    prefix = "⚠️" if fn == WARN else "❌"
                    entity_issues.append(f"- {prefix} entity row {i+1}: {msg.strip()}")
        if entity_issues:
            a(f"### Entity warnings / errors")
            for line in entity_issues:
                a(line)
            a(f"")

    # ── Planned actions summary ───────────────────────────────────────────────
    a(f"## Planned database updates")
    a(f"")
    a(f"Data from workbook `{wb_path.name}` will update the following tables in `{DB_PATH.name}`:")
    a(f"")
    a(f"| Table | Action | Rows |")
    a(f"|---|---|---|")

    site_action = ("INSERT 1 new site" if site_result["action"] == "insert"
                   else f"REUSE existing site_id={site_result['site_id']} (no INSERT)")
    a(f"| `site` | {site_action} | {'1' if site_result['action'] == 'insert' else '—'} |")

    n_entity_insert = sum(1 for er in entity_results if er["action"] == "insert")
    a(f"| `entity` | INSERT | {n_entity_insert} |")

    if dating_result is not None:
        ids = dating_result["dating_ids"]
        a(f"| `dating` | INSERT (dating_id {ids[0]}–{ids[-1]}) | {len(ids)} |")

    if sample_result is not None:
        ids = sample_result["sample_ids"]
        a(f"| `sample` | INSERT (sample_id {ids[0]}–{ids[-1]}) | {len(ids)} |")
        a(f"| `original_chronology` | INSERT | {len(ids)} |")
        if sample_result["hiatus_count"]:
            a(f"| `hiatus` | INSERT | {sample_result['hiatus_count']} |")
        if sample_result["gap_count"]:
            a(f"| `gap` | INSERT | {sample_result['gap_count']} |")
        for table, count in sample_result["proxy_counts"].items():
            if count > 0:
                a(f"| `{table}` | INSERT | {count} |")

    if lamina_result is not None and lamina_result["lamina_ids"]:
        ids = lamina_result["lamina_ids"]
        a(f"| `dating_lamina` | INSERT (dating_lamina_id {ids[0]}–{ids[-1]}) | {len(ids)} |")

    if ref_result is not None:
        new_refs = sum(1 for r in ref_result["rows"] if r["action"] == "insert")
        reused   = len(ref_result["rows"]) - new_refs
        a(f"| `reference` | INSERT {new_refs} new, REUSE {reused} | {new_refs} |")
        a(f"| `entity_link_reference` | INSERT | {len(ref_result['rows'])} |")
        a(f"")
        a(f"### Reference validation (CrossRef)")
        a(f"")
        for r in ref_result["rows"]:
            doi_str = r["doi"] or "no DOI"
            # Find the DOI validation message for this row
            doi_msgs = [msg for fn, msg in ref_result["messages"] if fn == OK and "validated" in msg]
            warn_msgs = [msg for fn, msg in ref_result["messages"] if fn == WARN and ("DOI" in msg or "author" in msg or "title" in msg)]
            err_msgs  = [msg for fn, msg in ref_result["messages"] if fn == ERR  and "DOI" in msg]
            if err_msgs:
                status = f"❌ {err_msgs[0].strip()}"
            elif doi_msgs:
                status = f"✅ {doi_msgs[0].strip()}"
            elif warn_msgs:
                status = f"⚠️ {warn_msgs[0].strip()}"
            else:
                status = "⚠️ No DOI — not validated"
            a(f"- `{doi_str}` — {status}")

    if notes_result is not None and notes_result["to_insert"]:
        a(f"| `notes` | INSERT | {len(notes_result['to_insert'])} |")

    a(f"")

    # ── Commit record ─────────────────────────────────────────────────────────
    a(f"## Commit record")
    a(f"")
    a(f"<!-- COMMIT_STAMP -->")
    a(f"_Not yet committed._")
    a(f"")

    log_path.write_text("\n".join(lines), encoding="utf-8")
    return log_path


def stamp_log_committed(log_path: Path):
    """Replace the placeholder in the log file with the actual commit timestamp."""
    text = log_path.read_text(encoding="utf-8")
    stamp_line = f"**Committed:** {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    text = text.replace(
        "<!-- COMMIT_STAMP -->\n_Not yet committed._",
        f"<!-- COMMIT_STAMP -->\n{stamp_line}",
    )
    # Also update the Outcome line
    text = text.replace(
        "⏳ Dry-run only — pending commit decision",
        "✅ Committed",
    )
    log_path.write_text(text, encoding="utf-8")


# ── Actual write ───────────────────────────────────────────────────────────────
def commit_changes(
    wb_path: Path,
    data: dict,
    site_result: dict,
    entity_results: list[dict],
    dating_result: dict | None = None,
    sample_result: dict | None = None,
    lamina_result: dict | None = None,
    ref_result: dict | None = None,
    notes_result: dict | None = None,
    note_texts: list[str] | None = None,
):
    print(f"\nConnecting to {DB_PATH} …")
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # ── Site ──────────────────────────────────────────────────────────────────
    if site_result["action"] == "insert":
        df_site = data["Site metadata"]
        row = df_site.iloc[0]
        cur.execute(
            "INSERT INTO site (site_id, site_name, latitude, longitude, elevation, monitoring) "
            "VALUES (?,?,?,?,?,?)",
            (str(site_result["site_id"]), str(row["site_name"]),
             str(row["latitude"]), str(row["longitude"]),
             str(row["elevation"]), str(row.get("monitoring", ""))),
        )
        print(OK(f"Inserted site '{row['site_name']}' (site_id={site_result['site_id']})"))

    site_id = site_result["site_id"]

    # ── Entities ──────────────────────────────────────────────────────────────
    df_entity = data["Entity metadata"]
    # Get DB column names
    cur.execute("PRAGMA table_info(entity)")
    db_cols = {r[1] for r in cur.fetchall()}

    for i, er in enumerate(entity_results):
        if er["action"] != "insert":
            print(WARN(f"Skipping entity (see pre-flight report)"))
            continue

        row = df_entity.iloc[i]
        one_and_only = str(row.get("one_and_only", "yes")).lower().strip()
        status_info  = str(row.get("entity_status_info", "not applicable")).lower().strip()
        status_notes = str(row.get("entity_status_notes", "")).strip()

        # Determine entity_status
        if one_and_only == "yes" or status_info in ("not applicable", "nan"):
            entity_status = "current"
        elif status_info == "current":
            entity_status = "current"
        elif status_info == "superseded":
            entity_status = "superseded"
        else:
            entity_status = "current"

        # Build insert dict — only columns that exist in DB
        insert = {
            "site_id":     str(site_id),
            "entity_id":   str(er["entity_id"]),
            "entity_name": str(row["entity_name"]).strip(),
            "entity_status": entity_status,
            "corresponding_current": None,
            "persist_id":  er["persist_id"],
            # v15 workbook always measures depth from top
            "depth_ref":   "from top",
        }
        for col in ENTITY_WORKBOOK_COLS:
            if col in db_cols and col not in insert:
                insert[col] = clean_val(row.get(col))

        cols_sql = ", ".join(insert.keys())
        placeholders = ", ".join("?" * len(insert))
        cur.execute(
            f"INSERT INTO entity ({cols_sql}) VALUES ({placeholders})",
            list(insert.values()),
        )
        print(OK(f"Inserted entity '{row['entity_name']}' (entity_id={er['entity_id']})"))

        # Handle superseded entities
        if status_info == "current" and status_notes:
            for old_id in status_notes.split(";"):
                old_id = old_id.strip()
                if old_id:
                    cur.execute(
                        "UPDATE entity SET entity_status='superseded', "
                        "corresponding_current=? WHERE entity_id=?",
                        (str(er["entity_id"]), old_id),
                    )
                    print(OK(f"  Set entity_id={old_id} to 'superseded', corresponding_current={er['entity_id']}"))

    # ── Dating ────────────────────────────────────────────────────────────────
    if "Dating information" in data and dating_result is not None:
        df_dating = data["Dating information"]
        cur.execute("PRAGMA table_info(dating)")
        dating_db_cols = {r[1] for r in cur.fetchall()}

        # Build name→entity_id map: newly inserted entities are now in DB
        entity_map = {}
        for name in df_dating["entity_name"].dropna().unique():
            name = str(name).strip()
            cur.execute(
                "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
                (str(site_id), name),
            )
            row = cur.fetchone()
            if row:
                entity_map[name] = int(row[0])

        for i, (_, drow) in enumerate(df_dating.iterrows()):
            ename = str(drow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Dating row {i+1}: entity '{ename}' not in DB — skipping"))
                continue

            dating_id = dating_result["dating_ids"][i]
            insert = {
                "dating_id": str(dating_id),
                "entity_id": str(eid),
            }
            for col in dating_db_cols:
                if col in insert:
                    continue
                if col in _DATING_WORKBOOK_ONLY or col in _DATING_AGEMODEL_COLS:
                    insert[col] = None
                elif col in df_dating.columns:
                    insert[col] = clean_val(drow.get(col))
                else:
                    insert[col] = None

            cols_sql = ", ".join(f'"{c}"' for c in insert.keys())
            placeholders = ", ".join("?" * len(insert))
            cur.execute(
                f"INSERT INTO dating ({cols_sql}) VALUES ({placeholders})",
                list(insert.values()),
            )

        print(OK(f"Inserted {len(df_dating)} dating rows"))

    # ── Sample data ───────────────────────────────────────────────────────────
    if "Sample data" in data and sample_result is not None:
        df_sample = data["Sample data"]

        # Rebuild entity_map now that entities are committed
        entity_map = {}
        for name in df_sample["entity_name"].dropna().unique():
            name = str(name).strip()
            cur.execute(
                "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
                (str(site_id), name),
            )
            row = cur.fetchone()
            if row:
                entity_map[name] = int(row[0])

        proxy_inserted = {t: 0 for t in _PROXY_TABLES}
        hiatus_inserted = 0
        gap_inserted = 0

        for i, (_, srow) in enumerate(df_sample.iterrows()):
            ename = str(srow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Sample row {i+1}: entity '{ename}' not in DB — skipping"))
                continue

            sid = sample_result["sample_ids"][i]

            # sample
            cur.execute(
                "INSERT INTO sample (entity_id, sample_id, depth_sample, mineralogy, arag_corr, sample_thickness) "
                "VALUES (?,?,?,?,?,?)",
                (str(eid), str(sid),
                 clean_val(srow.get("depth_sample")),
                 clean_val(srow.get("mineralogy")),
                 clean_val(srow.get("arag_corr")),
                 clean_val(srow.get("sample_thickness"))),
            )

            # original_chronology
            cur.execute(
                "INSERT INTO original_chronology "
                "(sample_id, interp_age, interp_age_uncert_pos, interp_age_uncert_neg, "
                "age_model_type, ann_lam_check, dep_rate_check) VALUES (?,?,?,?,?,?,?)",
                (str(sid),
                 clean_val(srow.get("interp_age")),
                 clean_val(srow.get("interp_age_uncert_pos")),
                 clean_val(srow.get("interp_age_uncert_neg")),
                 clean_val(srow.get("age_model_type")),
                 clean_val(srow.get("ann_lam_check")),
                 clean_val(srow.get("dep_rate_check"))),
            )

            # hiatus
            h = srow.get("hiatus")
            if h is not None and not (isinstance(h, float) and pd.isna(h)):
                cur.execute(
                    "INSERT INTO hiatus (sample_id, hiatus) VALUES (?,?)",
                    (str(sid), str(h)),
                )
                hiatus_inserted += 1

            # gap
            g = srow.get("gap")
            if g is not None and not (isinstance(g, float) and pd.isna(g)):
                cur.execute(
                    "INSERT INTO gap (sample_id, gap) VALUES (?,?)",
                    (str(sid), str(g)),
                )
                gap_inserted += 1

            # proxy tables
            for table, (meas_col, prec_col) in _PROXY_TABLES.items():
                meas = srow.get(meas_col)
                if meas is None or (isinstance(meas, float) and pd.isna(meas)):
                    continue
                prec = srow.get(prec_col)
                cur.execute(
                    f'INSERT INTO "{table}" (sample_id, {meas_col}, {prec_col}) VALUES (?,?,?)',
                    (str(sid), str(meas), clean_val(prec)),
                )
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

    # ── Lamina age vs depth ───────────────────────────────────────────────────
    if "Lamina age vs depth" in data and lamina_result is not None:
        df_lamina = data["Lamina age vs depth"]
        entity_map = {}
        for name in df_lamina["entity_name"].dropna().unique():
            name = str(name).strip()
            cur.execute(
                "SELECT entity_id FROM entity WHERE site_id=? AND entity_name=?",
                (str(site_id), name),
            )
            row = cur.fetchone()
            if row:
                entity_map[name] = int(row[0])

        for i, (_, lrow) in enumerate(df_lamina.iterrows()):
            ename = str(lrow["entity_name"]).strip()
            eid = entity_map.get(ename)
            if eid is None:
                print(WARN(f"Lamina row {i+1}: entity '{ename}' not in DB — skipping"))
                continue
            lid = lamina_result["lamina_ids"][i]
            cur.execute(
                "INSERT INTO dating_lamina "
                "(dating_lamina_id, entity_id, depth_lam, lam_thickness, "
                "lam_age, lam_age_uncert_pos, lam_age_uncert_neg) VALUES (?,?,?,?,?,?,?)",
                (str(lid), str(eid),
                 clean_val(lrow.get("depth_lam")),
                 clean_val(lrow.get("lam_thickness")),
                 clean_val(lrow.get("lam_age")),
                 clean_val(lrow.get("lam_age_uncert_pos")),
                 clean_val(lrow.get("lam_age_uncert_neg"))),
            )
        print(OK(f"Inserted {len(df_lamina)} lamina rows"))

    # ── References ────────────────────────────────────────────────────────────
    if ref_result is not None:
        n_inserted = 0
        n_linked = 0
        for r in ref_result["rows"]:
            if r["action"] == "insert":
                cur.execute(
                    "INSERT INTO reference (ref_id, citation, publication_DOI) VALUES (?,?,?)",
                    (str(r["ref_id"]), r["citation"], r["doi"]),
                )
                n_inserted += 1
            # Always link entity → reference (check not already linked)
            cur.execute(
                "SELECT 1 FROM entity_link_reference WHERE entity_id=? AND ref_id=?",
                (str(r["entity_id"]), str(r["ref_id"])),
            )
            if not cur.fetchone():
                cur.execute(
                    "INSERT INTO entity_link_reference (entity_id, ref_id) VALUES (?,?)",
                    (str(r["entity_id"]), str(r["ref_id"])),
                )
                n_linked += 1
        print(OK(f"References: {n_inserted} inserted, {n_linked} entity links added"))

    # ── Notes ─────────────────────────────────────────────────────────────────
    if notes_result is not None and note_texts:
        for text in notes_result["to_insert"]:
            cur.execute(
                "INSERT INTO notes (site_id, notes) VALUES (?,?)",
                (str(site_id), text),
            )
        if notes_result["to_insert"]:
            print(OK(f"Inserted {len(notes_result['to_insert'])} note(s) for site_id={site_id}"))

    con.commit()
    con.close()
    print(OK("DB committed."))

    # ── Update CSVs — re-export all tables ───────────────────────────────────
    con2 = sqlite3.connect(DB_PATH)
    cur2 = con2.cursor()
    cur2.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    all_tables = [r[0] for r in cur2.fetchall()]
    con2.close()
    for table in all_tables:
        _update_csv(table, DB_PATH, CSV_DIR)
    print(OK(f"All {len(all_tables)} tables exported to CSVs in {CSV_DIR}"))


def _update_csv(table: str, db_path: Path, csv_dir: Path):
    """Re-export a single table from SQLite to its CSV file."""
    csv_path = csv_dir / f"{table}.csv"
    # Backup existing
    if csv_path.exists():
        shutil.copy(csv_path, csv_path.with_suffix(".csv.bak"))
    con = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {table}", con)
    con.close()
    df.to_csv(csv_path, index=False, quoting=csv.QUOTE_NONNUMERIC, na_rep="NA")
    print(INFO(f"  Wrote {len(df)} rows → {csv_path.name}"))


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="SISAL DB update importer")
    parser.add_argument("--workbook", required=True, help="Path to QC-passed workbook (.xlsx)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                      help="Run checks only, write nothing (default)")
    mode.add_argument("--commit",  dest="dry_run", action="store_false",
                      help="Write to DB and update CSVs after passing checks")
    args = parser.parse_args()

    wb_path = Path(args.workbook)
    if not wb_path.exists():
        print(ERR(f"Workbook not found: {wb_path}"))
        sys.exit(1)
    if not DB_PATH.exists():
        print(ERR(f"Database not found: {DB_PATH}"))
        sys.exit(1)

    print(f"\nReading workbook: {wb_path.name} …")
    data = read_workbook(wb_path)
    print(f"  Sheets loaded: {list(data.keys())}")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # ── Site ──────────────────────────────────────────────────────────────────
    if "Site metadata" not in data:
        print(ERR("No 'Site metadata' sheet found or it is empty."))
        con.close()
        sys.exit(1)

    site_row = data["Site metadata"].iloc[0]
    site_result = check_site(cur, site_row)

    # ── Entities ──────────────────────────────────────────────────────────────
    if "Entity metadata" not in data:
        print(ERR("No 'Entity metadata' sheet found or it is empty."))
        con.close()
        sys.exit(1)

    entity_results = []
    for _, erow in data["Entity metadata"].iterrows():
        if pd.isna(erow.get("entity_name")):
            continue
        er = check_entity(cur, erow, site_result["site_id"])
        entity_results.append(er)

    # ── Dating ────────────────────────────────────────────────────────────────
    dating_result = None
    if "Dating information" in data:
        dating_result = check_dating(
            cur, data["Dating information"], site_result["site_id"], entity_results
        )

    # ── Sample data ───────────────────────────────────────────────────────────
    sample_result = None
    if "Sample data" in data:
        sample_result = check_sample(
            cur, data["Sample data"], site_result["site_id"], entity_results
        )

    # ── Lamina age vs depth ───────────────────────────────────────────────────
    lamina_result = None
    if "Lamina age vs depth" in data and not data["Lamina age vs depth"].empty:
        lamina_result = check_lamina(
            cur, data["Lamina age vs depth"], site_result["site_id"], entity_results
        )

    # ── References ────────────────────────────────────────────────────────────
    ref_result = None
    if "References" in data and not data["References"].empty:
        ref_result = check_references(
            cur, data["References"], entity_results, site_result["site_id"]
        )

    # ── Notes (read raw — no header row) ──────────────────────────────────────
    notes_result = None
    note_texts = []
    xls = pd.ExcelFile(wb_path)
    if "Notes" in xls.sheet_names:
        raw = pd.read_excel(xls, sheet_name="Notes", header=None)
        note_texts = [str(v).strip() for v in raw.iloc[:, 0].dropna() if str(v).strip()]
        if note_texts:
            notes_result = check_notes(cur, note_texts, site_result["site_id"])

    con.close()

    # ── Report ─────────────────────────────────────────────────────────────────
    errors, warnings = preflight_report(
        wb_path, site_result, entity_results, dating_result, sample_result,
        lamina_result, ref_result, notes_result
    )

    log_path = write_preflight_log(
        wb_path, data, site_result, entity_results, errors, warnings,
        dating_result, sample_result, lamina_result, ref_result, notes_result
    )
    print(INFO(f"Pre-flight log written → {log_path}"))

    if not args.dry_run:
        if errors:
            print(ERR("Aborting --commit due to errors in pre-flight report."))
            sys.exit(1)
        commit_changes(
            wb_path, data, site_result, entity_results,
            dating_result, sample_result, lamina_result,
            ref_result, notes_result, note_texts
        )
        stamp_log_committed(log_path)
        print(INFO(f"Log updated with commit timestamp → {log_path}"))
    else:
        print("Dry run complete. Re-run with --commit to apply changes.\n")


if __name__ == "__main__":
    main()
