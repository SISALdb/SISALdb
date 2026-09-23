"""Interactively add a new project entry: name, status, dates, people + roles,
and the list of SISAL entities it covers.

Edits the CSV source of truth directly (csv/projects.csv, csv/project_person.csv,
csv/project_link_entity.csv) -- NOT the compiled sisalv3.1.db, which is a build
artifact regenerated from these CSVs and would silently discard any direct edit
on the next `build_db.py` run.

Run from the repo root:
    uv run python DS_scripts/add_project.py
    (or: python3 DS_scripts/add_project.py)

After running, rebuild and verify before committing:
    python3 USER_scripts/build_db.py /tmp/sisal_check
"""
import csv
import re
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    openpyxl = None

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = REPO_ROOT / "csv"

PROJECTS_CSV = CSV_DIR / "projects.csv"
PROJECT_PERSON_CSV = CSV_DIR / "project_person.csv"
PROJECT_LINK_ENTITY_CSV = CSV_DIR / "project_link_entity.csv"
PERSON_CSV = CSV_DIR / "person.csv"
ENTITY_CSV = CSV_DIR / "entity.csv"

PROJECT_STATUS_CHOICES = ["active", "closed"]
# Same contributor_role_enum used by release_person -- kept in sync manually,
# see ROLE in build_db.py.
ROLE_CHOICES = [
    "release_steward",
    "data_curator",
    "workflow_developer",
    "project_lead",
    "data_contributor",
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_csv(path, fieldnames, rows, use_bom=False):
    encoding = "utf-8-sig" if use_bom else "utf-8"
    with open(path, "w", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


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


def prompt_choice(text, choices, default=None):
    choice_str = "/".join(choices)
    while True:
        val = prompt(f"{text} ({choice_str})", required=default is None, default=default)
        val = val.lower()
        if val in choices:
            return val
        print(f"  Please enter one of: {choice_str}")


def prompt_date(text, required=True):
    while True:
        val = input(f"{text} (YYYY-MM-DD, blank to skip): ").strip()
        if not val:
            if required:
                print("  This is required -- please enter a date.")
                continue
            return ""
        if not DATE_RE.match(val):
            print("  Please use YYYY-MM-DD format.")
            continue
        try:
            datetime.strptime(val, "%Y-%m-%d")
        except ValueError:
            print("  Not a real calendar date -- try again.")
            continue
        return val


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


def find_person(people_rows, query):
    """Case-insensitive substring match on name. Returns list of matching rows."""
    q = query.strip().lower()
    return [r for r in people_rows if q in r["name"].lower()]


def collect_people(people_rows):
    print("\n--- People involved ---")
    print("For each person, search by (partial) name; pick from matches or add a new person.")
    collected = []  # list of (person_id, name, role)
    while True:
        query = prompt("\nSearch for a person by name (blank to finish adding people)", required=False)
        if not query:
            break
        matches = find_person(people_rows, query)
        if not matches:
            print(f"  No existing person matches '{query}'.")
            if prompt_yes_no(f"  Add '{query}' as a brand-new person?", default_yes=True):
                new_id = str(max((int(r["person_id"]) for r in people_rows), default=0) + 1)
                orcid = prompt("  ORCID (blank if unknown)", required=False)
                new_row = {"person_id": new_id, "name": query, "orcid": orcid}
                people_rows.append(new_row)
                chosen = new_row
            else:
                continue
        elif len(matches) == 1:
            chosen = matches[0]
            print(f"  Matched: {chosen['name']} (person_id {chosen['person_id']}, ORCID {chosen['orcid'] or 'unknown'})")
        else:
            print(f"  {len(matches)} matches:")
            for i, m in enumerate(matches, 1):
                print(f"    {i}. {m['name']} (person_id {m['person_id']}, ORCID {m['orcid'] or 'unknown'})")
            while True:
                idx = prompt("  Pick a number")
                if idx.isdigit() and 1 <= int(idx) <= len(matches):
                    chosen = matches[int(idx) - 1]
                    break
                print("  Invalid choice.")

        role = prompt_choice("  Role for this person on this project", ROLE_CHOICES)
        collected.append((chosen["person_id"], chosen["name"], role))
        print(f"  Added: {chosen['name']} as {role}")

    return collected


def find_entities(entity_rows, query):
    """Match entity_id exactly; else entity_name exactly (case-insensitive); else
    entity_name by substring. Each tier only falls through to the next if it finds
    nothing -- otherwise a short, valid, exact name (e.g. "A1") would get swallowed
    into unrelated substring collisions ("SPA121" also contains "a1") instead of
    matching the entity that's actually named exactly that."""
    q = query.strip().lower()
    exact_id = [r for r in entity_rows if r["entity_id"] == query.strip()]
    if exact_id:
        return exact_id
    exact_name = [r for r in entity_rows if (r["entity_name"] or "").strip().lower() == q]
    if exact_name:
        return exact_name
    return [r for r in entity_rows if q in (r["entity_name"] or "").lower()]


def read_tokens_from_file(path_str):
    """Read a list of entity id/name tokens from column A of an .xlsx or .csv/.txt
    file. Returns a list of non-empty string tokens, or None if the file couldn't
    be read (caller should fall back to another input method)."""
    path = Path(path_str.strip().strip('"').strip("'")).expanduser()
    if not path.exists():
        print(f"  File not found: {path}")
        return None

    raw_values = []
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        if openpyxl is None:
            print("  openpyxl isn't installed -- can't read .xlsx. Try a .csv/.txt export instead.")
            return None
        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[wb.sheetnames[0]]
        if len(wb.sheetnames) > 1:
            print(f"  Note: workbook has {len(wb.sheetnames)} sheets, reading the first one ('{ws.title}').")
        for row in ws.iter_rows(min_col=1, max_col=1, values_only=True):
            val = row[0]
            if val is not None and str(val).strip():
                raw_values.append(str(val).strip())
        wb.close()
    elif suffix in (".csv", ".tsv", ".txt"):
        delimiter = "\t" if suffix == ".tsv" else ","
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.reader(f, delimiter=delimiter):
                if row and row[0].strip():
                    raw_values.append(row[0].strip())
    else:
        print(f"  Unrecognized file type '{suffix}' -- expected .xlsx, .csv, .tsv, or .txt.")
        return None

    if not raw_values:
        print("  No values found in column A of that file.")
        return None

    print(f"\n  Read {len(raw_values)} value(s) from column A, first 5:")
    for v in raw_values[:5]:
        print(f"    - {v}")
    if prompt_yes_no("  Does the first row look like a header (e.g. 'entity_name') rather than real data?", default_yes=False):
        raw_values = raw_values[1:]
        print(f"  Skipping header, {len(raw_values)} value(s) remain.")

    return raw_values


def resolve_entity_token(entity_rows, query, seen_ids, linked):
    """Resolve one entity_id/name token interactively (disambiguating if needed)
    and append it to `linked` in place if found and not already added."""
    matches = find_entities(entity_rows, query)
    if not matches:
        print(f"  No entity matches '{query}' -- check the ID/name and try again.")
        return
    if len(matches) == 1:
        chosen = matches[0]
    else:
        print(f"  '{query}' -- {len(matches)} matches:")
        for i, m in enumerate(matches, 1):
            print(f"    {i}. entity_id {m['entity_id']} -- {m['entity_name']}")
        while True:
            idx = prompt("  Pick a number")
            if idx.isdigit() and 1 <= int(idx) <= len(matches):
                chosen = matches[int(idx) - 1]
                break
            print("  Invalid choice.")

    if chosen["entity_id"] in seen_ids:
        print(f"  entity_id {chosen['entity_id']} already added, skipping.")
        return
    seen_ids.add(chosen["entity_id"])
    linked.append((chosen["entity_id"], chosen["entity_name"]))
    print(f"  Added: entity_id {chosen['entity_id']} ({chosen['entity_name']})")


def collect_entities(entity_rows):
    print("\n--- SISAL entities linked to this project ---")
    linked = []  # list of (entity_id, entity_name)
    seen_ids = set()

    if prompt_yes_no("Do you have a file with the list (Excel .xlsx or .csv/.txt)?", default_yes=True):
        while True:
            file_path = prompt("  Path to the file", required=False)
            if not file_path:
                break
            tokens = read_tokens_from_file(file_path)
            if tokens is not None:
                print(f"\nResolving {len(tokens)} entries from the file...")
                for tok in tokens:
                    resolve_entity_token(entity_rows, tok, seen_ids, linked)
                break
            if not prompt_yes_no("  Try a different file path?", default_yes=True):
                break

    print("\nYou can also paste a list directly (comma- and/or newline-separated,")
    print("entity IDs or entity names, mixed is fine) -- press Enter to skip this.")
    print("(End a multi-line paste with an empty line.)")
    pasted_lines = []
    while True:
        line = input("> ")
        if not line.strip():
            break
        pasted_lines.append(line)

    if pasted_lines:
        tokens = []
        for line in pasted_lines:
            tokens.extend(t.strip() for t in line.split(",") if t.strip())
        print(f"\nResolving {len(tokens)} pasted entries...")
        for tok in tokens:
            resolve_entity_token(entity_rows, tok, seen_ids, linked)

    print("\nAdd more one at a time (blank line to finish), or just press Enter if you're done.")
    while True:
        query = prompt("\nEntity (id or name, blank to finish)", required=False)
        if not query:
            break
        resolve_entity_token(entity_rows, query, seen_ids, linked)

    return linked


def main():
    proj_fields, proj_rows = read_csv(PROJECTS_CSV)
    pp_fields, pp_rows = read_csv(PROJECT_PERSON_CSV)
    ple_fields, ple_rows = read_csv(PROJECT_LINK_ENTITY_CSV)
    person_fields, person_rows = read_csv(PERSON_CSV)
    entity_fields, entity_rows = read_csv(ENTITY_CSV)

    print("=== New SISAL project entry ===\n")

    project_name = prompt("Project name")
    status = prompt_choice("Project status", PROJECT_STATUS_CHOICES, default="active")

    start_date = prompt_date("Start date")
    if status == "closed":
        completion_date = prompt_date("Completion date")
    else:
        if prompt_yes_no("Add a (planned/target) completion date anyway?", default_yes=False):
            completion_date = prompt_date("Completion date", required=False)
        else:
            completion_date = ""

    project_doi = prompt("Project DOI (blank if none yet)", required=False)
    project_notes = prompt("Project notes (blank to skip)", required=False)

    people = collect_people(person_rows)
    entities = collect_entities(entity_rows)

    # --- confirm ---
    new_project_id = str(max((int(r["project_id"]) for r in proj_rows), default=0) + 1)

    print("\n=== Summary ===")
    print(f"project_id:       {new_project_id}")
    print(f"project_name:     {project_name}")
    print(f"project_status:   {status}")
    print(f"start_date:       {start_date or '(none)'}")
    print(f"completion_date:  {completion_date or '(none)'}")
    print(f"project_doi:      {project_doi or '(none)'}")
    print(f"project_notes:    {project_notes or '(none)'}")
    print(f"people ({len(people)}):")
    for pid, name, role in people:
        print(f"  - {name} (person_id {pid}) -- {role}")
    print(f"linked entities ({len(entities)}):")
    for eid, ename in entities:
        print(f"  - entity_id {eid} ({ename})")

    if not prompt_yes_no("\nWrite this to the CSVs?", default_yes=True):
        print("Aborted -- nothing written.")
        return

    # --- append rows ---
    proj_rows.append({
        "project_id": new_project_id,
        "project_name": project_name,
        "project_doi": project_doi,
        "project_status": status,
        "start_date": start_date,
        "completion_date": completion_date,
        "project_notes": project_notes,
    })
    for pid, _name, role in people:
        pp_rows.append({"project_id": new_project_id, "person_id": pid, "role": role})
    for eid, _ename in entities:
        ple_rows.append({"project_id": new_project_id, "entity_id": eid})

    write_csv(PROJECTS_CSV, proj_fields, proj_rows, use_bom=True)
    write_csv(PROJECT_PERSON_CSV, pp_fields, pp_rows)
    write_csv(PROJECT_LINK_ENTITY_CSV, ple_fields, ple_rows)
    # person.csv may have gained a brand-new person row -- keep its BOM (Excel-safe accents).
    write_csv(PERSON_CSV, person_fields, person_rows, use_bom=True)

    print(f"\nDone. Project '{project_name}' added as project_id {new_project_id}.")
    print("Next: rebuild and verify before committing:")
    print("  python3 USER_scripts/build_db.py /tmp/sisal_check")


if __name__ == "__main__":
    main()
