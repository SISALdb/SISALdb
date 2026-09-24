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

    --dry-run   (default) Look up every site, print a preview of what would
                change, write nothing.
    --commit    After a clean dry-run, write the resolved values into
                csv/entity.csv.

Only fills entities whose `wokam` is currently blank -- an entity that
already has a value (e.g. from a future manual override) is left alone and
reported separately, never silently overwritten.

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
# These are WOKAM's own published legend categories (BGR/WHYMAP), which map
# almost 1:1 onto wokam_enum -- but VERIFY against the actual downloaded
# shapefile before trusting this: shapefile column/attribute names and exact
# label text can differ by data vintage. The script below checks both
# FIELD_NAME and every raw value it encounters against this map and refuses
# to guess on a mismatch (prints what it actually found so you can fix the
# two constants below), so a wrong assumption here fails loudly rather than
# writing bad data.
FIELD_NAME = "ROCK_TYPE"
RAW_TO_ENUM = {
    "Continuous carbonate rocks": "continuous carbonate",
    "Discontinuous carbonate rocks": "discontinuous carbonate",
    "Continuous evaporite rocks": "continuous evaporite",
    "Discontinuous evaporite rocks": "discontinuous evaporite",
    "Mixed carbonate and evaporite rocks": "mixed carbonate and evaporite",
    # "Other rocks" (non-karst, includes local/shallow karst) has no
    # wokam_enum equivalent -- sites landing here are left blank (None),
    # same as sites outside the shapefile's coverage entirely.
    "Other rocks, includes areas with local and shallow karst": None,
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


def find_shapefile(data_dir):
    shapefiles = sorted(data_dir.glob("*.shp"))
    if not shapefiles:
        print(f"No .shp file found in {data_dir}")
        print("See DS_scripts/release_finalization/data/README.md to download WOKAM.")
        sys.exit(1)
    if len(shapefiles) > 1:
        print(f"More than one .shp file in {data_dir} -- using the first: {shapefiles[0].name}")
    return shapefiles[0]


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


def resolve_site_wokam(shapefile_path):
    """Returns {site_id: wokam_value_or_None} for every site in csv/site.csv,
    via point-in-polygon lookup against the WOKAM shapefile."""
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        print("Missing dependency -- install with:\n    pip install geopandas shapely")
        sys.exit(1)

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
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True,
                       help="Preview only, write nothing (default)")
    mode.add_argument("--commit", dest="dry_run", action="store_false",
                       help="Write resolved values into csv/entity.csv")
    args = parser.parse_args()

    schema_enum = load_wokam_enum(SCHEMA_DBML)
    mapped_values = {v for v in RAW_TO_ENUM.values() if v is not None}
    drift = mapped_values - set(schema_enum)
    if drift:
        print(f"RAW_TO_ENUM produces value(s) not in schema wokam_enum: {sorted(drift)}")
        print(f"Schema wokam_enum is: {schema_enum}")
        sys.exit(1)

    shapefile_path = find_shapefile(DATA_DIR)
    print(f"Using shapefile: {shapefile_path.name}")
    site_to_wokam = resolve_site_wokam(shapefile_path)

    entity_fields, entity_rows = read_csv(CSV_DIR / "entity.csv")
    if "wokam" not in entity_fields:
        print("entity.csv has no 'wokam' column -- has the schema changed?")
        sys.exit(1)

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

    if args.dry_run:
        print(f"\nDry run -- no changes written. Re-run with --commit to apply {len(to_update)} update(s).")
        return

    for row, val in to_update:
        row["wokam"] = val
    write_csv(CSV_DIR / "entity.csv", entity_fields, entity_rows)
    print(f"\nWrote {len(to_update)} update(s) to csv/entity.csv")
    print("Next: rebuild and verify -- python3 USER_scripts/build_db.py /tmp/sisal_check")


if __name__ == "__main__":
    main()
