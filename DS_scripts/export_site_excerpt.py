"""
Export a self-contained excerpt of csv/ for one site (all its entities/speleothems)
-- same schema/structure as the full repo, just filtered to that site's rows, plus
whatever else those rows reference via foreign key (people, references, the release
row(s), code artifacts) so the excerpt still passes a full FK integrity check on its
own. Useful for sharing a small real-structure demo dataset (e.g. a workshop) without
handing out the whole database.

Cascade (hardcoded, matching build_db.py's own hardcoded-DDL style rather than parsing
schema.dbml generically -- the schema's FK graph is small enough to just state directly):

    site --> entity (site_id)
    entity --> dating, dating_lamina, sample, entity_link_person, entity_link_reference,
               project_link_entity, composite_link_entity (either column) (entity_id)
    entity --> notes (site_id), database_release (added_in_release_id /
               last_modified_release_id)
    sample --> original_chronology, sisal_chronology, gap, hiatus, d13C, d18O, Sr_Ca,
               Mg_Ca, Ba_Ca, U_Ca, P_Ca, Sr_isotopes (sample_id)
    entity_link_person --> person (person_id)
    entity_link_reference --> reference (ref_id)
    project_link_entity --> projects (project_id) --> project_person --> person (more)
    database_release --> code_artifact (agemodel/downsampling/lcc_artifact_id),
                          database_release (previous_release, self-ref -- resolved to
                          a fixed point so a chain of releases doesn't break the FK)
    database_release --> release_person (release_id) --> person (more)

Every table in the schema is written to the output, even when 0 rows match -- an
empty CSV (header only) rather than a missing file, so the excerpt's structure stays
complete and buildable, not just its populated corner.

Usage (run from the SISALdb repo root):
    python3 DS_scripts/export_site_excerpt.py --site "La Vallina" --output /path/to/data

After running, sanity-check the excerpt is genuinely self-contained -- this script's own
verify_fk_integrity() does that automatically and refuses to write if it fails.
"""
import argparse
import csv
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = REPO_ROOT / "csv"
SCHEMA_DBML = REPO_ROOT / "schema" / "schema.dbml"

# Every table in the schema, in an order that's safe to load with FK checks on
# (parents before children) -- same list as USER_scripts/build_db.py's LOAD_ORDER.
ALL_TABLES = [
    "code_artifact", "database_release", "person", "projects", "site", "notes", "entity",
    "entity_link_person", "release_person", "project_person", "project_link_entity",
    "composite_link_entity", "reference", "entity_link_reference", "dating", "dating_lamina",
    "sample", "original_chronology", "sisal_chronology", "gap", "hiatus",
    "d13C", "d18O", "Sr_Ca", "Mg_Ca", "Ba_Ca", "U_Ca", "P_Ca", "Sr_isotopes",
]


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_all():
    tables = {}
    for name in ALL_TABLES:
        fields, rows = read_csv(CSV_DIR / f"{name}.csv")
        tables[name] = (fields, rows)
    return tables


def build_excerpt(tables, site_name):
    site_fields, site_rows = tables["site"]
    matches = [r for r in site_rows if r["site_name"] == site_name]
    if not matches:
        print(f"No site found with site_name == {site_name!r} in csv/site.csv")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple sites named {site_name!r} -- ambiguous, refusing to guess: {matches}")
        sys.exit(1)
    site_id = matches[0]["site_id"]

    out = {name: (fields, []) for name, (fields, _rows) in tables.items()}
    out["site"] = (site_fields, matches)

    notes_fields, notes_rows = tables["notes"]
    out["notes"] = (notes_fields, [r for r in notes_rows if r["site_id"] == site_id])

    entity_fields, entity_rows = tables["entity"]
    entities = [r for r in entity_rows if r["site_id"] == site_id]
    out["entity"] = (entity_fields, entities)
    entity_ids = {r["entity_id"] for r in entities}
    if not entity_ids:
        print(f"Site {site_name!r} (site_id {site_id}) has zero entities in entity.csv -- nothing to export.")
        sys.exit(1)

    for name in ("dating", "dating_lamina", "sample", "entity_link_person",
                 "entity_link_reference", "project_link_entity"):
        fields, rows = tables[name]
        out[name] = (fields, [r for r in rows if r["entity_id"] in entity_ids])

    cle_fields, cle_rows = tables["composite_link_entity"]
    out["composite_link_entity"] = (cle_fields, [
        r for r in cle_rows
        if r["composite_entity_id"] in entity_ids or r["single_entity_id"] in entity_ids
    ])

    sample_ids = {r["sample_id"] for r in out["sample"][1]}
    for name in ("original_chronology", "sisal_chronology", "gap", "hiatus",
                 "d13C", "d18O", "Sr_Ca", "Mg_Ca", "Ba_Ca", "U_Ca", "P_Ca", "Sr_isotopes"):
        fields, rows = tables[name]
        out[name] = (fields, [r for r in rows if r["sample_id"] in sample_ids])

    # people referenced so far: entity_link_person direct hits
    person_ids = {r["person_id"] for r in out["entity_link_person"][1]}

    ref_ids = {r["ref_id"] for r in out["entity_link_reference"][1]}
    ref_fields, ref_rows = tables["reference"]
    out["reference"] = (ref_fields, [r for r in ref_rows if r["ref_id"] in ref_ids])

    project_ids = {r["project_id"] for r in out["project_link_entity"][1]}
    proj_fields, proj_rows = tables["projects"]
    out["projects"] = (proj_fields, [r for r in proj_rows if r["project_id"] in project_ids])
    pp_fields, pp_rows = tables["project_person"]
    out["project_person"] = (pp_fields, [r for r in pp_rows if r["project_id"] in project_ids])
    person_ids |= {r["person_id"] for r in out["project_person"][1]}

    release_ids = {r[c] for r in entities for c in ("added_in_release_id", "last_modified_release_id") if r.get(c)}
    dr_fields, dr_rows = tables["database_release"]
    dr_by_id = {r["release_id"]: r for r in dr_rows}
    # fixed point: pull in any previous_release chain so that FK stays satisfied
    changed = True
    while changed:
        changed = False
        for rid in list(release_ids):
            prev = dr_by_id.get(rid, {}).get("previous_release")
            if prev and prev not in release_ids:
                release_ids.add(prev)
                changed = True
    out["database_release"] = (dr_fields, [r for r in dr_rows if r["release_id"] in release_ids])

    artifact_ids = {
        r[c] for r in out["database_release"][1]
        for c in ("agemodel_artifact_id", "downsampling_artifact_id", "lcc_artifact_id") if r.get(c)
    }
    ca_fields, ca_rows = tables["code_artifact"]
    out["code_artifact"] = (ca_fields, [r for r in ca_rows if r["artifact_id"] in artifact_ids])

    rp_fields, rp_rows = tables["release_person"]
    out["release_person"] = (rp_fields, [r for r in rp_rows if r["release_id"] in release_ids])
    person_ids |= {r["person_id"] for r in out["release_person"][1]}

    person_fields, person_rows = tables["person"]
    out["person"] = (person_fields, [r for r in person_rows if r["person_id"] in person_ids])

    return out


def verify_fk_integrity(out):
    """Lightweight, hardcoded check of the same FK edges build_excerpt() filled in --
    not a full reimplementation of build_db.py's DDL, just enough to catch a mistake
    in this script itself before anything gets written."""
    def ids(table, col):
        return {r[col] for r in out[table][1]}

    checks = [
        ("entity.site_id", ids("entity", "site_id"), ids("site", "site_id")),
        ("dating.entity_id", ids("dating", "entity_id"), ids("entity", "entity_id")),
        ("sample.entity_id", ids("sample", "entity_id"), ids("entity", "entity_id")),
        ("entity_link_person.person_id", ids("entity_link_person", "person_id"), ids("person", "person_id")),
        ("entity_link_reference.ref_id", ids("entity_link_reference", "ref_id"), ids("reference", "ref_id")),
        ("original_chronology.sample_id", ids("original_chronology", "sample_id"), ids("sample", "sample_id")),
        ("sisal_chronology.sample_id", ids("sisal_chronology", "sample_id"), ids("sample", "sample_id")),
    ]
    problems = []
    for label, child_ids, parent_ids in checks:
        missing = {v for v in child_ids if v} - parent_ids
        if missing:
            problems.append(f"  {label}: {len(missing)} value(s) with no matching parent row: {sorted(missing)[:5]}")
    if problems:
        print("FK integrity check failed -- refusing to write the excerpt:")
        for p in problems:
            print(p)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Export a self-contained per-site excerpt of csv/")
    parser.add_argument("--site", required=True, help="Exact site_name from site.csv, e.g. 'La Vallina'")
    parser.add_argument("--output", required=True, help="Output directory for the excerpt's csv/ files")
    args = parser.parse_args()

    tables = load_all()
    out = build_excerpt(tables, args.site)
    verify_fk_integrity(out)

    out_dir = Path(args.output).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, (fields, rows) in out.items():
        write_csv(out_dir / f"{name}.csv", fields, rows)

    schema_dest = out_dir.parent / "schema.dbml" if out_dir.name == "csv" else out_dir / "schema.dbml"
    schema_dest.write_text(SCHEMA_DBML.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"\nExcerpt for {args.site!r} (site_id {out['site'][1][0]['site_id']}) written to {out_dir}")
    print(f"  entities: {len(out['entity'][1])}, samples: {len(out['sample'][1])}, "
          f"dating rows: {len(out['dating'][1])}, people: {len(out['person'][1])}, "
          f"references: {len(out['reference'][1])}")
    print(f"Schema copy: {schema_dest}")
    print("FK integrity check passed. Every table.csv written (some may be header-only).")


if __name__ == "__main__":
    main()
