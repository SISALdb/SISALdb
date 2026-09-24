"""
Backfill entity.wokam via point-in-polygon lookup against the WOKAM (World
Karst Aquifer Map) shapefile -- one piece of the release finalization
pipeline (see the "Release finalization pipeline" note in the SISAL-Neotoma
Obsidian vault for the other pieces: copernicus_lcc, SISAL.AM age models,
GitHub Actions auto-push).

Why geospatial lookup and not manual entry: `wokam` is a draft v3.1 entity
column (`wokam_enum` in schema/schema.dbml), currently blank for every
entity. WOKAM classifies karst by rock type/continuity as polygons, so each
site's (lat, lon) can be resolved to a value automatically instead of
someone looking it up by hand per site.

One-time data prep (not scripted -- the shapefile is too large to commit
and isn't ours to redistribute):
    See DS_scripts/release_finalization/data/README.md for the exact
    download link and where to unzip it. Short version: grab the WOKAM
    shapefile from the BGR/WHYMAP Geoportal and unzip it into
    DS_scripts/release_finalization/data/ (git-ignored).

Dependencies not currently in this repo (no requirements.txt yet --
install directly):
    pip install geopandas shapely

Usage (run from the repo root):
    python3 DS_scripts/release_finalization/get_wokam.py [--dry-run]
    python3 DS_scripts/release_finalization/get_wokam.py --commit
    python3 DS_scripts/release_finalization/get_wokam.py --validate

    --dry-run   (default) Look up every site, print a preview of what would
                change, write nothing.
    --commit    After a clean dry-run, write the resolved values into
                csv/entity.csv.
    --validate  Don't touch the blanks -- instead, re-resolve every entity
                that ALREADY has a wokam value (654/903 as of 2026-09-24,
                part of the original published SISALv3 data, not written by
                this script) and compare against the real value. Use this
                to sanity-check FIELD_NAME/RAW_TO_ENUM and the join itself
                against a large known-good set before trusting the 3-ish
                new fills a fresh shapefile download produces. Writes
                nothing either way.

Only fills entities whose `wokam` is currently blank -- an entity that
already has a value is left alone and reported separately, never silently
overwritten. Most entities already have one: `wokam` is an original
SISALv3 field, not a genuinely-all-blank v3.1 addition -- this script only
closes the remaining gaps.

After running, rebuild and verify before committing anyway -- this script's
own validation only covers the wokam_enum values it writes, not everything
else build_db.py checks:
    python3 USER_scripts/build_db.py /tmp/sisal_check
"""
import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CSV_DIR = REPO_ROOT / "csv"
SCHEMA_DBML = REPO_ROOT / "schema" / "schema.dbml"
DATA_DIR = Path(__file__).resolve().parent / "data"

# ---------------------------------------------------------------------------
# WOKAM shapefile attribute -> schema wokam_enum value.
#
# Confirmed 2026-09-24 against the real WHYMAP_WOKAM_v1 download: the karst
# rock-type polygon layer ships as `whymap_karst__v1_poly.shp`, WGS84
# (matches site.csv lat/lon, no reprojection needed), 2805 polygons, text
# labels in a `RTypeLabel` field exactly matching wokam_enum's 5 values
# (only capitalization/wording differs, mapped 1:1 below -- no "other rocks"
# catch-all in this layer, since it only contains karst outcrop polygons in
# the first place; a site outside all 2805 polygons is simply not karst and
# is left blank, same as any other "no match").
#
# If a future re-download changes the field name or label text, the script
# still refuses to guess rather than silently using this stale mapping (see
# find_shapefile / resolve_site_wokam below).
FIELD_NAME = "RTypeLabel"
RAW_TO_ENUM = {
    "Continuous carbonate rocks": "continuous carbonate",
    "Discontinuous carbonate rocks": "discontinuous carbonate",
    "Continuous evaporite rocks": "continuous evaporite",
    "Discontinuous evaporite rocks": "discontinuous evaporite",
    "Mixed carbonate and evaporite rocks": "mixed carbonate and evaporite",
}


# ---------------------------------------------------------------------------
def load_wokam_enum(path):
    """Schema-driven: pull the real wokam_enum values out of schema.dbml, so
    RAW_TO_ENUM above is checked against the actual schema rather than a
    hand-copied duplicate that could drift."""
    text = path.read_text(encoding="utf-8")
    m = re.search(r"Enum\s+wokam_enum\s*\{([^}]*)\}", text)
    if not m:
        print(f"Couldn't find 'Enum wokam_enum {{ ... }}' in {path}")
        sys.exit(1)
    return re.findall(r'"([^"]*)"', m.group(1))


def find_shapefile(data_dir, gpd):
    """The real WHYMAP_WOKAM_v1 download unzips into several .shp layers
    (cave points, spring points, non-exposed-karst points, and the actual
    karst rock-type polygons) -- pick the one that actually has FIELD_NAME,
    rather than guessing by filename or position."""
    shapefiles = sorted(data_dir.rglob("*.shp"))
    if not shapefiles:
        print(f"No .shp file found under {data_dir}")
        print("See DS_scripts/release_finalization/data/README.md to download WOKAM.")
        sys.exit(1)

    candidates = []
    columns_by_file = {}
    for path in shapefiles:
        cols = list(gpd.read_file(path, rows=1).columns)
        columns_by_file[path] = cols
        if FIELD_NAME in cols:
            candidates.append(path)

    if len(candidates) == 1:
        return candidates[0]

    print(f"Couldn't find exactly one .shp layer with a '{FIELD_NAME}' column under {data_dir}:")
    for path, cols in columns_by_file.items():
        marker = " <-- has it" if path in candidates else ""
        print(f"  {path.relative_to(data_dir)}: {cols}{marker}")
    if not candidates:
        print(f"Update FIELD_NAME at the top of this script to match one of the columns above.")
    else:
        print(f"{len(candidates)} layers match -- remove the extras from {data_dir} or narrow this further.")
    sys.exit(1)


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames, list(reader)


def write_csv(path, fieldnames, rows):
    # entity.csv carries no UTF-8 BOM today -- match that (see backfill_from_csv.py).
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def resolve_site_wokam(data_dir):
    """Returns {site_id: wokam_value_or_None} for every site in csv/site.csv,
    via point-in-polygon lookup against the WOKAM shapefile."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        print("Missing dependency -- install with:\n    pip install geopandas shapely")
        sys.exit(1)

    shapefile_path = find_shapefile(data_dir, gpd)
    print(f"Using shapefile: {shapefile_path.relative_to(data_dir)}")
    wokam_gdf = gpd.read_file(shapefile_path)
    if FIELD_NAME not in wokam_gdf.columns:
        print(f"Expected attribute column '{FIELD_NAME}' not found in {shapefile_path.name}.")
        print(f"Actual columns: {list(wokam_gdf.columns)}")
        print("Update FIELD_NAME (and RAW_TO_ENUM if needed) at the top of this script.")
        sys.exit(1)

    raw_values = set(wokam_gdf[FIELD_NAME].dropna().unique())
    unmapped = raw_values - set(RAW_TO_ENUM)
    if unmapped:
        print(f"{len(unmapped)} value(s) in '{FIELD_NAME}' aren't in RAW_TO_ENUM -- refusing to guess:")
        for v in sorted(unmapped):
            print(f"  {v!r}")
        print("Add them to RAW_TO_ENUM at the top of this script (or None to map to 'no match') and rerun.")
        sys.exit(1)

    site_fields, site_rows = read_csv(CSV_DIR / "site.csv")
    for col in ("site_id", "latitude", "longitude"):
        if col not in site_fields:
            print(f"site.csv is missing expected column '{col}'")
            sys.exit(1)

    sites_with_coords = [
        r for r in site_rows if r["latitude"].strip() and r["longitude"].strip()
    ]
    points = gpd.GeoDataFrame(
        {"site_id": [r["site_id"] for r in sites_with_coords]},
        geometry=[Point(float(r["longitude"]), float(r["latitude"])) for r in sites_with_coords],
        crs="EPSG:4326",
    )
    if wokam_gdf.crs is not None and wokam_gdf.crs != points.crs:
        points = points.to_crs(wokam_gdf.crs)

    joined = gpd.sjoin(points, wokam_gdf[[FIELD_NAME, "geometry"]], how="left", predicate="within")

    site_to_wokam = {}
    for _, row in joined.iterrows():
        raw = row[FIELD_NAME]
        site_to_wokam[row["site_id"]] = RAW_TO_ENUM.get(raw) if raw is not None else None
    # Sites without coordinates in site.csv simply aren't in the lookup --
    # callers treat a missing key the same as "no match" (left blank).
    return site_to_wokam


def main():
    parser = argparse.ArgumentParser(description="Backfill entity.wokam via WOKAM point-in-polygon lookup")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run",
                       help="Preview only, write nothing (default)")
    mode.add_argument("--commit", dest="mode", action="store_const", const="commit",
                       help="Write resolved values into csv/entity.csv")
    mode.add_argument("--validate", dest="mode", action="store_const", const="validate",
                       help="Re-resolve entities that already have a value and report agreement -- writes nothing")
    args = parser.parse_args()

    schema_enum = load_wokam_enum(SCHEMA_DBML)
    mapped_values = {v for v in RAW_TO_ENUM.values() if v is not None}
    drift = mapped_values - set(schema_enum)
    if drift:
        print(f"RAW_TO_ENUM produces value(s) not in schema wokam_enum: {sorted(drift)}")
        print(f"Schema wokam_enum is: {schema_enum}")
        sys.exit(1)

    site_to_wokam = resolve_site_wokam(DATA_DIR)

    entity_fields, entity_rows = read_csv(CSV_DIR / "entity.csv")
    if "wokam" not in entity_fields:
        print("entity.csv has no 'wokam' column -- has the schema changed?")
        sys.exit(1)

    if args.mode == "validate":
        run_validate(entity_rows, site_to_wokam)
        return

    to_update = []      # (row, new_value)
    already_set = []    # (entity_id, existing_value)
    no_site_match = []  # entity_id -- site_id not found in site_to_wokam at all
    no_polygon_match = []  # entity_id -- resolved, but landed outside any WOKAM polygon

    for row in entity_rows:
        entity_id = row["entity_id"]
        existing = row["wokam"].strip()
        if existing:
            already_set.append((entity_id, existing))
            continue
        site_id = row["site_id"]
        if site_id not in site_to_wokam:
            no_site_match.append(entity_id)
            continue
        new_value = site_to_wokam[site_id]
        if new_value is None:
            no_polygon_match.append(entity_id)
            continue
        to_update.append((row, new_value))

    print("\n=== Preview ===")
    print(f"Entities already set (left alone):    {len(already_set)}")
    print(f"Entities with no site coordinates:    {len(no_site_match)}")
    print(f"Entities outside any WOKAM polygon:   {len(no_polygon_match)}")
    print(f"Entities to update:                   {len(to_update)}")

    if to_update:
        print("\nBreakdown of new values:")
        counts = {}
        for _row, val in to_update:
            counts[val] = counts.get(val, 0) + 1
        for val, n in sorted(counts.items()):
            print(f"  {val}: {n}")

        print("\nFirst 10 changes:")
        for row, val in to_update[:10]:
            print(f"  entity_id {row['entity_id']} (site_id {row['site_id']}): wokam -> '{val}'")

    if not to_update:
        print("\nNothing to update.")
        return

    if args.mode == "dry-run":
        print(f"\nDry run -- no changes written. Re-run with --commit to apply {len(to_update)} update(s).")
        return

    for row, val in to_update:
        row["wokam"] = val
    write_csv(CSV_DIR / "entity.csv", entity_fields, entity_rows)
    print(f"\nWrote {len(to_update)} update(s) to csv/entity.csv")
    print("Next: rebuild and verify -- python3 USER_scripts/build_db.py /tmp/sisal_check")


def run_validate(entity_rows, site_to_wokam):
    """Re-resolves every entity that ALREADY has a wokam value and compares
    against the real value -- a correctness check for FIELD_NAME/RAW_TO_ENUM
    and the spatial join, using the 654 already-populated entities as a
    known-good set. Writes nothing."""
    agree = []       # (entity_id, value)
    disagree = []    # (entity_id, site_id, existing, resolved)
    unresolvable = []  # (entity_id, site_id, existing) -- resolved to None (outside any polygon)

    for row in entity_rows:
        existing = row["wokam"].strip()
        if not existing:
            continue
        site_id = row["site_id"]
        resolved = site_to_wokam.get(site_id)
        if resolved is None:
            unresolvable.append((row["entity_id"], site_id, existing))
        elif resolved == existing:
            agree.append((row["entity_id"], existing))
        else:
            disagree.append((row["entity_id"], site_id, existing, resolved))

    checked = len(agree) + len(disagree) + len(unresolvable)
    print("\n=== Validation against already-populated entities ===")
    print(f"Entities checked (already had a value): {checked}")
    print(f"  Agree:                {len(agree)}")
    print(f"  Disagree:             {len(disagree)}")
    print(f"  Unresolvable (outside any WOKAM polygon): {len(unresolvable)}")

    if disagree:
        print("\nDisagreements (existing value vs. what the shapefile resolves to now):")
        for entity_id, site_id, existing, resolved in disagree:
            print(f"  entity_id {entity_id} (site_id {site_id}): existing '{existing}' vs resolved '{resolved}'")

    if checked:
        rate = len(agree) / (len(agree) + len(disagree)) * 100 if (agree or disagree) else 0.0
        print(f"\nAgreement rate (excluding unresolvable): {rate:.1f}%")


if __name__ == "__main__":
    main()
