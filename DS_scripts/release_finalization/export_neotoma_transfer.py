"""
Export the SISAL -> Neotoma transfer files: one flat CSV per entity that is new or
modified since the published SISALv3.0, plus a MANIFEST, for Neotoma's DataBUS.
Part of the release finalization pipeline, alongside get_wokam.py and
get_copernicus_lcc.py.

Neotoma already holds the published SISALv3.0, so DataBUS only needs what has
changed since then. The default is to run this once at the end, for all changed
entities (--changed). Single entities can be exported with --entity, e.g. right
after an add_entity.py import.

Read-only: never writes to csv/. Builds a throwaway SQLite database from a
**committed** ref (default HEAD) with USER_scripts/build_db.py (refuses on FK
violations), so uncommitted work, such as a running age-model batch, never ends
up in an export.

"Changed since v3.0" is measured against commit V30_BASELINE (0ec2680), the first
data commit of this repo: the plain v3.0 -> v3.1 migration, verified on 2026-09-27
to have 0 added/removed/changed rows against the published SISALv3.0 CSVs. Rows
are matched by primary key (from schema/schema.dbml); values are normalised
before comparing (NA/NULL/empty, 3000.0 == 3000, whitespace).
An entity counts as changed when a row of its own changed: entity,
entity_link_reference, dating, dating_lamina, sample, composite_link_entity, or
any per-sample table (isotopes, trace elements, chronologies, hiatus, gap ...).
Changes in shared tables (site, notes, reference, person, entity_link_person)
are listed in the README with the entities they touch, but those entities are
only exported with --include-indirect. Example: the ORCID backfill touched the
contact of most entities.

Output (git-ignored), in output/neotoma_transfer/<date>_<commit>/:
    sisal_entity_<entity_id>.csv   one per entity (naming of the v3 DataBUS import)
    MANIFEST.csv                   entity, site, new/modified, what changed, rows
    README.txt                     source commit, scope, indirect changes, notes

The query is sql/neotoma_flat_export.sql: Laura Endres' canonical flat-export
query, one row per depth, plus `contact` / `contact_orcid` from the person
registry. Each table.* in it keeps its own key columns, so the raw result repeats
column names (sample_id, site_id, the entity row via the self-join compe).
Same-named columns are merged when their non-empty values agree on every row;
any that disagree are kept with a suffix (name__2) and listed in the README.

No third-party dependencies (standard library + git).

Usage (run from the repo root):
    python3 DS_scripts/release_finalization/export_neotoma_transfer.py                 # --changed at HEAD
    python3 DS_scripts/release_finalization/export_neotoma_transfer.py --ref origin/main
    python3 DS_scripts/release_finalization/export_neotoma_transfer.py --entity 903 --entity 904
    python3 DS_scripts/release_finalization/export_neotoma_transfer.py --list          # what would be exported
    python3 DS_scripts/release_finalization/export_neotoma_transfer.py --include-indirect
"""
import argparse
import collections
import csv
import io
import math
import re
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SQL_FILE = Path(__file__).resolve().parent / "sql" / "neotoma_flat_export.sql"
V30_BASELINE = "0ec2680"  # plain v3.0 -> v3.1 migration = published SISALv3.0 data

MISSING = {"", "NA", "NULL", "null", "None", "nan", "NaN"}
# Tables whose rows belong to one entity directly (table -> entity column).
ENTITY_COL = {
    "entity": "entity_id", "dating": "entity_id", "dating_lamina": "entity_id",
    "sample": "entity_id", "entity_link_reference": "entity_id",
    "composite_link_entity": "composite_entity_id",
}
# Shared tables: a change there touches entities only indirectly.
INDIRECT = {"site", "notes", "reference", "person", "entity_link_person"}
# Bookkeeping tables, not part of the export.
IGNORE = {"database_release", "release_person", "code_artifact", "projects", "project_person",
          "project_link_entity"}
# Tables renamed since the baseline (baseline name -> current name).
RENAMED = {"entity_person": "entity_link_person"}


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                          capture_output=True, text=True).stdout


def read_table(ref, table):
    text = git("show", f"{ref}:csv/{table}.csv").lstrip("﻿")
    reader = csv.reader(io.StringIO(text))
    header = [h.lstrip("﻿").strip() for h in next(reader)]
    return header, [dict(zip(header, r)) for r in reader if r]


def tables_at(ref):
    return {Path(p).stem for p in git("ls-tree", "--name-only", ref, "csv/").split() if p.endswith(".csv")}


def primary_keys(dbml):
    keys = {}
    for m in re.finditer(r"Table\s+(\w+)\s*\{(.*?)\n\}", dbml, re.S):
        name, body = m.group(1), m.group(2)
        comp = re.search(r"\(([\w,\s]+)\)\s*\[pk\]", body)
        if comp:
            keys[name] = [c.strip() for c in comp.group(1).split(",")]
            continue
        single = [ln.split()[0] for ln in body.splitlines() if re.search(r"\[[^\]]*\bpk\b", ln) and ln.split()]
        if single:
            keys[name] = single
    return keys


def norm(v):
    if v is None:
        return ""
    v = v.strip()
    if v in MISSING:
        return ""
    try:
        f = float(v)
    except ValueError:
        return v
    if math.isnan(f):
        return ""
    return str(int(f)) if f.is_integer() and abs(f) < 1e15 else repr(f)


def keyed(rows, key):
    return {tuple(norm(r.get(c)) for c in key): r for r in rows}


def diff_entities(ref, since):
    """Return (new, per_entity, indirect): per_entity[eid][table] = [added, removed, changed]."""
    pks = primary_keys(git("show", f"{ref}:schema/schema.dbml"))
    now, then = tables_at(ref), tables_at(since)
    old_name = {RENAMED.get(t, t): t for t in then}
    res = {}
    for t in sorted(now - IGNORE):
        if t not in old_name:
            continue
        h0, r0 = read_table(since, old_name[t])
        h1, r1 = read_table(ref, t)
        key = pks.get(t) or [c for c in h1 if c in h0]
        a, b = keyed(r0, key), keyed(r1, key)
        shared = [c for c in h1 if c in h0 and c not in key]
        changed = [k for k in a.keys() & b.keys() if any(norm(a[k].get(c)) != norm(b[k].get(c)) for c in shared)]
        res[t] = {"key": key, "old": a, "new": b, "added": [k for k in b if k not in a],
                  "removed": [k for k in a if k not in b], "changed": changed}

    sample_entity = {}
    for rows in (res["sample"]["old"], res["sample"]["new"]):
        for k, r in rows.items():
            sample_entity[k[0]] = norm(r.get("entity_id"))

    # entities per site / reference / person, from both versions
    site_ents, ref_ents, person_ents = (collections.defaultdict(set) for _ in range(3))
    for rows in (res["entity"]["old"], res["entity"]["new"]):
        for r in rows.values():
            site_ents[norm(r.get("site_id"))].add(norm(r.get("entity_id")))
    for rows in (res["entity_link_reference"]["old"], res["entity_link_reference"]["new"]):
        for r in rows.values():
            ref_ents[norm(r.get("ref_id"))].add(norm(r.get("entity_id")))
    if "entity_link_person" in res:
        for rows in (res["entity_link_person"]["old"], res["entity_link_person"]["new"]):
            for r in rows.values():
                person_ents[norm(r.get("person_id"))].add(norm(r.get("entity_id")))

    per_entity = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    indirect = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0, 0]))
    for t, r in res.items():
        for i, bucket in enumerate(("added", "removed", "changed")):
            for k in r[bucket]:
                row = r["new"].get(k) or r["old"].get(k)
                if t in ENTITY_COL:
                    per_entity[norm(row.get(ENTITY_COL[t]))][t][i] += 1
                elif r["key"] == ["sample_id"]:
                    per_entity[sample_entity.get(k[0], "?")][t][i] += 1
                elif t in ("site", "notes"):
                    for e in site_ents[norm(row.get("site_id"))]:
                        indirect[e][t][i] += 1
                elif t == "reference":
                    for e in ref_ents[norm(row.get("ref_id"))]:
                        indirect[e][t][i] += 1
                elif t == "person":
                    for e in person_ents[norm(row.get("person_id"))]:
                        indirect[e][t][i] += 1
                elif t == "entity_link_person":
                    indirect[norm(row.get("entity_id"))][t][i] += 1
    new = {k[0] for k in res["entity"]["added"]}
    return new, per_entity, indirect


def describe(tables):
    parts = []
    for t, (a, r, c) in sorted(tables.items()):
        parts.append(f"{t} " + " ".join(s for s in (f"+{a}" if a else "", f"-{r}" if r else "", f"~{c}" if c else "") if s))
    return "; ".join(parts)


def build_db(ref, workdir):
    """Materialise csv/, schema/, USER_scripts/ at `ref` and run build_db.py there."""
    src = workdir / "repo"
    src.mkdir()
    archive = subprocess.run(["git", "-C", str(REPO), "archive", ref, "csv", "schema", "USER_scripts"],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(src)], input=archive, check=True)
    out = workdir / "db"
    r = subprocess.run([sys.executable, str(src / "USER_scripts" / "build_db.py"), str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0 or "0 foreign key violations" not in r.stdout:
        sys.exit(f"build_db.py failed or reported violations:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
    return out / "sisalv3.1.db"


def merge_duplicate_columns(header, rows):
    """Merge same-named columns whose non-empty values agree on every row."""
    def empty(v):
        return v is None or v == ""

    groups = {}
    for i, h in enumerate(header):
        groups.setdefault(h, []).append(i)
    out_cols, conflicts = [], []
    for name, idxs in groups.items():
        buckets = []
        for i in idxs:
            for b in buckets:
                if all(empty(r[i]) or all(empty(r[j]) or str(r[j]) == str(r[i]) for j in b) for r in rows):
                    b.append(i)
                    break
            else:
                buckets.append([i])
        for k, b in enumerate(buckets):
            col = name if k == 0 else f"{name}__{k + 1}"
            if k:
                conflicts.append(col)
            out_cols.append((col, b))
    new_rows = [[next((r[i] for i in b if not empty(r[i])), None) for _, b in out_cols] for r in rows]
    return [c for c, _ in out_cols], new_rows, conflicts


def main():
    p = argparse.ArgumentParser(description="SISAL -> Neotoma transfer export (changes since v3.0)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--changed", action="store_true",
                      help="(default) all entities new or modified since v3.0")
    mode.add_argument("--entity", type=int, action="append", default=[], help="only these entity_ids (repeatable)")
    p.add_argument("--include-indirect", action="store_true",
                   help="with --changed: also export entities touched only via site/notes/reference/contact changes")
    p.add_argument("--ref", default="HEAD", help="committed ref to export (default: %(default)s)")
    p.add_argument("--since", default=V30_BASELINE, help="baseline ref = SISALv3.0 (default: %(default)s)")
    p.add_argument("--list", action="store_true", help="only list what would be exported, write nothing")
    p.add_argument("--out-dir", type=Path, help="default: output/neotoma_transfer/<date>_<commit>/")
    args = p.parse_args()

    commit = git("rev-parse", "--short", args.ref).strip()
    pending = git("status", "--porcelain", "--", "csv/").strip()
    if pending and args.ref == "HEAD":
        print(f"Note: csv/ has uncommitted changes; they are NOT exported (ref {commit}).")

    new, direct, indirect = diff_entities(args.ref, args.since)
    direct.pop("?", None)
    indirect_only = sorted((e for e in indirect if e not in direct), key=float)
    if args.entity:
        ids = [str(e) for e in args.entity]
    else:
        ids = sorted(direct, key=float) + (indirect_only if args.include_indirect else [])

    def status(e):
        return "new" if e in new else "modified" if e in direct else \
            "indirect" if e in indirect else "unchanged since v3.0"

    if args.list:
        for e in ids:
            print(f"{e:>5}  {status(e):<20} {describe(direct.get(e) or indirect.get(e) or {})}")
        print(f"\n{len(ids)} entities at {commit}; {len(indirect_only)} more touched only indirectly"
              f"{' (included)' if args.include_indirect else ' (use --include-indirect)'}")
        return
    if not ids:
        sys.exit(f"No entities changed since v3.0 at {commit}; nothing to export.")

    out_dir = args.out_dir or REPO / "output" / "neotoma_transfer" / f"{date.today():%Y-%m-%d}_{commit}"
    out_dir.mkdir(parents=True, exist_ok=True)
    sql = SQL_FILE.read_text()
    manifest, conflict_notes = [], []
    with tempfile.TemporaryDirectory() as tmp:
        con = sqlite3.connect(build_db(args.ref, Path(tmp)))
        for e in ids:
            meta = con.execute("select e.entity_name, s.site_id, s.site_name from entity e join site s using (site_id) "
                               "where e.entity_id = ?", (int(e),)).fetchone()
            if meta is None:
                sys.exit(f"entity_id {e} not found at {commit}")
            cur = con.execute(sql, (int(e),))
            header, rows, conflicts = merge_duplicate_columns([d[0] for d in cur.description], cur.fetchall())
            out = out_dir / f"sisal_entity_{e}.csv"
            with open(out, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(rows)
            count = lambda col: sum(1 for r in rows if r[header.index(col)] not in (None, ""))
            manifest.append({"entity_id": e, "entity_name": meta[0], "site_id": meta[1], "site_name": meta[2],
                             "status": status(e), "changes_since_v3": describe(direct.get(e) or {}),
                             "indirect_changes": describe(indirect.get(e) or {}),
                             "rows": len(rows), "samples": count("depth_sample"), "dates": count("depth_dating"),
                             "columns": len(header), "file": out.name})
            if conflicts:
                conflict_notes.append(f"  {out.name}: {', '.join(conflicts)}")
            print(f"{e:>5} {meta[0]:<10} {status(e):<9} {len(rows):>5} rows x {len(header)} cols -> {out.name}")
        con.close()

    with open(out_dir / "MANIFEST.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(manifest[0]))
        w.writeheader()
        w.writerows(manifest)

    ind_lines = [f"  {e}: {describe(indirect[e])}" for e in indirect_only]
    (out_dir / "README.txt").write_text(
        f"SISAL -> Neotoma transfer export, {date.today():%Y-%m-%d}\n"
        f"Source: SISALdb commit {commit} ({args.ref}), built with USER_scripts/build_db.py (0 FK violations).\n"
        f"Only committed data; uncommitted csv/ changes are not included.\n"
        f"Baseline (= published SISALv3.0, already in Neotoma): commit {args.since}.\n\n"
        f"Scope: {'entities ' + ', '.join(ids) + ' (--entity)' if args.entity else 'all entities new or modified since v3.0'}"
        f"{' plus entities touched only indirectly' if args.include_indirect else ''}.\n"
        f"Files: one sisal_entity_<entity_id>.csv per entity; MANIFEST.csv lists new/modified and what changed\n"
        f"(table +added -removed ~changed rows).\n\n"
        f"Query: DS_scripts/release_finalization/sql/neotoma_flat_export.sql (one row per depth; sample, dating\n"
        f"and lamina rows aligned on depth; contact + contact_orcid from the person registry).\n"
        f"Same-named columns from the query's table.* joins are merged where their values agree on every row.\n"
        f"Columns kept with a suffix because values differed: "
        f"{chr(10) + chr(10).join(conflict_notes) if conflict_notes else 'none'}\n\n"
        f"Entities touched only through shared tables (site, notes, reference, person, entity_link_person),\n"
        f"{'included' if args.include_indirect else 'NOT exported (use --include-indirect)'}: {len(ind_lines)}\n"
        + ("\n".join(ind_lines) + "\n" if ind_lines else "") +
        "\nAges are years BP (1950). Fields may contain line breaks inside quotes (site notes): read with a\n"
        "CSV parser, not line by line.\n")
    print(f"\n{len(manifest)} entities -> {out_dir}\ncommit {commit} ({args.ref}); "
          f"{len(indirect_only)} entities touched only indirectly "
          f"({'included' if args.include_indirect else 'not exported'})")


if __name__ == "__main__":
    main()
