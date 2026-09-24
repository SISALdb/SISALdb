"""
Backfill entity.copernicus_lcc via per-site raster value lookup against the
Copernicus Global Land Cover (CGLS-LC100) discrete classification -- second
piece of the release finalization pipeline, alongside get_wokam.py (see the
"Release finalization pipeline" note in the SISAL-Neotoma Obsidian vault).

Like `wokam`, `copernicus_lcc` is an original published SISALv3 field, not
genuinely blank -- as of 2026-09-24 it's 887/903 filled, 16 blank. This
script closes gaps, it doesn't build the column from scratch.

One-time data prep (not scripted -- the raster tiles are too large to
commit and aren't ours to redistribute):
    See DS_scripts/release_finalization/data/copernicus_lcc/README.md for
    the download portal and tile-selection notes. Short version: grab only
    the CGLS-LC100 GeoTIFF tile(s) covering SISAL's actual site locations
    from https://lcviewer.vito.be/ and drop them into
    DS_scripts/release_finalization/data/copernicus_lcc/ (git-ignored).

Dependencies not currently in this repo (no requirements.txt yet --
install directly):
    pip install rasterio

Usage (run from the repo root):
    python3 DS_scripts/release_finalization/get_copernicus_lcc.py [--dry-run]
    python3 DS_scripts/release_finalization/get_copernicus_lcc.py --commit
    python3 DS_scripts/release_finalization/get_copernicus_lcc.py --validate

    --dry-run   (default) Look up every site, print a preview of what would
                change, write nothing.
    --commit    After a clean dry-run, write the resolved values into
                csv/entity.csv.
    --validate  Don't touch the blanks -- instead, re-resolve every entity
                that ALREADY has a copernicus_lcc value and compare against
                the real value, to sanity-check CODE_TO_ENUM and the raster
                sampling against a large known-good set. Writes nothing.

Only fills entities whose `copernicus_lcc` is currently blank -- an entity
that already has a value is left alone and reported separately, never
silently overwritten.

After running, rebuild and verify before committing anyway -- this script's
own validation only covers the copernicus_lcc_enum values it writes, not
everything else build_db.py checks:
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
DATA_DIR = Path(__file__).resolve().parent / "data" / "copernicus_lcc"

# ---------------------------------------------------------------------------
# CGLS-LC100 (Collection 3) discrete classification code -> schema
# copernicus_lcc_enum value. These are the product's own published class
# codes (Buchhorn et al. 2020) -- verify against whatever tile(s) you
# actually download before trusting this: if a raster's values fall outside
# this map, the script refuses to guess (lists the unmapped codes actually
# encountered) rather than writing something wrong.
CODE_TO_ENUM = {
    0: "unknown",
    20: "shrubs",
    30: "herbaceous vegetation",
    40: "cultivated and managed vegetation / agriculture",
    50: "urban / built up",
    60: "bare / sparse vegetation",
    70: "snow and ice",
    80: "permanent water bodies",
    90: "herbaceous wetland",
    100: "moss and lichen",
    111: "closed forest, evergreen needle leaf",
    112: "closed forest, evergreen broad leaf",
    113: "closed forest, deciduous needle leaf",
    114: "closed forest, deciduous broad leaf",
    115: "closed forest, mixed",
    116: "closed forest, other",
    121: "open forest, evergreen needle leaf",
    122: "open forest, evergreen broad leaf",
    123: "open forest, deciduous needle leaf",
    124: "open forest, deciduous broad leaf",
    125: "open forest, mixed",
    # 126 is "open forest, not matching any of the other definitions" in the
    # product legend -- schema's "open forest, other" (same pattern as 116 /
    # "closed forest, other").
    126: "open forest, other",
    200: "ocean",
}


# ---------------------------------------------------------------------------
def load_enum(path, enum_name):
    """Schema-driven: pull real enum values out of schema.dbml, so
    CODE_TO_ENUM above is checked against the actual schema rather than a
    hand-copied duplicate that could drift."""
    text = path.read_text(encoding="utf-8")
    m = re.search(rf"Enum\s+{enum_name}\s*\{{([^}}]*)\}}", text)
    if not m:
        print(f"Couldn't find 'Enum {enum_name} {{ ... }}' in {path}")
        sys.exit(1)
    return re.findall(r'"([^"]*)"', m.group(1))


def find_rasters(data_dir):
    rasters = sorted(list(data_dir.rglob("*.tif")) + list(data_dir.rglob("*.tiff")))
    if not rasters:
        print(f"No .tif/.tiff file found under {data_dir}")
        print("See DS_scripts/release_finalization/data/copernicus_lcc/README.md to download tiles.")
        sys.exit(1)
    return rasters


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


def resolve_site_lcc(data_dir):
    """Returns {site_id: (value_or_None, reason)} for every site in
    csv/site.csv with coordinates, via raster point sampling against
    whichever CGLS-LC100 tile(s) cover it. reason is one of:
    'ok', 'no_tile_coverage' (no downloaded tile's bounds contain the
    point), 'nodata_pixel' (a tile covers it, but the pixel is the
    raster's own nodata/fill value)."""
    try:
        import rasterio
    except ImportError:
        print("Missing dependency -- install with:\n    pip install rasterio")
        sys.exit(1)

    raster_paths = find_rasters(data_dir)
    datasets = [rasterio.open(p) for p in raster_paths]
    print(f"Using {len(datasets)} raster tile(s): {', '.join(p.name for p in raster_paths)}")

    site_fields, site_rows = read_csv(CSV_DIR / "site.csv")
    for col in ("site_id", "latitude", "longitude"):
        if col not in site_fields:
            print(f"site.csv is missing expected column '{col}'")
            sys.exit(1)

    site_to_lcc = {}
    encountered_codes = set()
    for row in site_rows:
        lat_s, lon_s = row["latitude"].strip(), row["longitude"].strip()
        if not lat_s or not lon_s:
            continue
        lat, lon = float(lat_s), float(lon_s)

        ds = next((d for d in datasets if d.bounds.left <= lon <= d.bounds.right
                   and d.bounds.bottom <= lat <= d.bounds.top), None)
        if ds is None:
            site_to_lcc[row["site_id"]] = (None, "no_tile_coverage", (lat, lon))
            continue

        value = next(ds.sample([(lon, lat)]))[0]
        if ds.nodata is not None and value == ds.nodata:
            site_to_lcc[row["site_id"]] = (None, "nodata_pixel", (lat, lon))
            continue

        encountered_codes.add(int(value))
        site_to_lcc[row["site_id"]] = (int(value), "ok", (lat, lon))

    for ds in datasets:
        ds.close()

    unmapped = encountered_codes - set(CODE_TO_ENUM)
    if unmapped:
        print(f"{len(unmapped)} raster code(s) encountered aren't in CODE_TO_ENUM -- refusing to guess:")
        for v in sorted(unmapped):
            print(f"  {v!r}")
        print("Add them to CODE_TO_ENUM at the top of this script and rerun.")
        sys.exit(1)

    return {
        site_id: (CODE_TO_ENUM[code] if reason == "ok" else None, reason, latlon)
        for site_id, (code, reason, latlon) in site_to_lcc.items()
    }


def main():
    parser = argparse.ArgumentParser(description="Backfill entity.copernicus_lcc via CGLS-LC100 raster lookup")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="mode", action="store_const", const="dry-run", default="dry-run",
                       help="Preview only, write nothing (default)")
    mode.add_argument("--commit", dest="mode", action="store_const", const="commit",
                       help="Write resolved values into csv/entity.csv")
    mode.add_argument("--validate", dest="mode", action="store_const", const="validate",
                       help="Re-resolve entities that already have a value and report agreement -- writes nothing")
    args = parser.parse_args()

    schema_enum = load_enum(SCHEMA_DBML, "copernicus_lcc_enum")
    drift = set(CODE_TO_ENUM.values()) - set(schema_enum)
    if drift:
        print(f"CODE_TO_ENUM produces value(s) not in schema copernicus_lcc_enum: {sorted(drift)}")
        print(f"Schema copernicus_lcc_enum is: {schema_enum}")
        sys.exit(1)

    site_to_lcc = resolve_site_lcc(DATA_DIR)

    entity_fields, entity_rows = read_csv(CSV_DIR / "entity.csv")
    if "copernicus_lcc" not in entity_fields:
        print("entity.csv has no 'copernicus_lcc' column -- has the schema changed?")
        sys.exit(1)

    if args.mode == "validate":
        run_validate(entity_rows, site_to_lcc)
        return

    to_update = []          # (row, new_value)
    already_set = []        # (entity_id, existing_value)
    no_site_match = []      # entity_id -- site_id not found in site_to_lcc at all
    no_tile_coverage = []   # (entity_id, lat, lon)
    nodata_pixel = []       # (entity_id, lat, lon)

    for row in entity_rows:
        entity_id = row["entity_id"]
        existing = row["copernicus_lcc"].strip()
        if existing:
            already_set.append((entity_id, existing))
            continue
        site_id = row["site_id"]
        if site_id not in site_to_lcc:
            no_site_match.append(entity_id)
            continue
        new_value, reason, latlon = site_to_lcc[site_id]
        if reason == "no_tile_coverage":
            no_tile_coverage.append((entity_id, *latlon))
            continue
        if reason == "nodata_pixel":
            nodata_pixel.append((entity_id, *latlon))
            continue
        to_update.append((row, new_value))

    print("\n=== Preview ===")
    print(f"Entities already set (left alone):    {len(already_set)}")
    print(f"Entities with no site coordinates:    {len(no_site_match)}")
    print(f"Entities with no raster tile coverage: {len(no_tile_coverage)}")
    print(f"Entities on a nodata/fill pixel:       {len(nodata_pixel)}")
    print(f"Entities to update:                   {len(to_update)}")

    if no_tile_coverage:
        print("\nNo raster tile covers these -- download the tile(s) for these coordinates and rerun:")
        for entity_id, lat, lon in no_tile_coverage[:20]:
            print(f"  entity_id {entity_id}: ({lat}, {lon})")
        if len(no_tile_coverage) > 20:
            print(f"  ... and {len(no_tile_coverage) - 20} more")

    if to_update:
        print("\nBreakdown of new values:")
        counts = {}
        for _row, val in to_update:
            counts[val] = counts.get(val, 0) + 1
        for val, n in sorted(counts.items()):
            print(f"  {val}: {n}")

        print("\nFirst 10 changes:")
        for row, val in to_update[:10]:
            print(f"  entity_id {row['entity_id']} (site_id {row['site_id']}): copernicus_lcc -> '{val}'")

    if not to_update:
        print("\nNothing to update.")
        return

    if args.mode == "dry-run":
        print(f"\nDry run -- no changes written. Re-run with --commit to apply {len(to_update)} update(s).")
        return

    for row, val in to_update:
        row["copernicus_lcc"] = val
    write_csv(CSV_DIR / "entity.csv", entity_fields, entity_rows)
    print(f"\nWrote {len(to_update)} update(s) to csv/entity.csv")
    print("Next: rebuild and verify -- python3 USER_scripts/build_db.py /tmp/sisal_check")


def run_validate(entity_rows, site_to_lcc):
    """Re-resolves every entity that ALREADY has a copernicus_lcc value and
    compares against the real value -- a correctness check for
    CODE_TO_ENUM and the raster sampling, using the 887 already-populated
    entities as a known-good set. Writes nothing."""
    agree = []          # (entity_id, value)
    disagree = []       # (entity_id, site_id, existing, resolved)
    unresolvable = []   # (entity_id, site_id, existing, reason)

    for row in entity_rows:
        existing = row["copernicus_lcc"].strip()
        if not existing:
            continue
        site_id = row["site_id"]
        if site_id not in site_to_lcc:
            continue
        resolved, reason, _latlon = site_to_lcc[site_id]
        if resolved is None:
            unresolvable.append((row["entity_id"], site_id, existing, reason))
        elif resolved == existing:
            agree.append((row["entity_id"], existing))
        else:
            disagree.append((row["entity_id"], site_id, existing, resolved))

    checked = len(agree) + len(disagree) + len(unresolvable)
    print("\n=== Validation against already-populated entities ===")
    print(f"Entities checked (already had a value): {checked}")
    print(f"  Agree:                {len(agree)}")
    print(f"  Disagree:             {len(disagree)}")
    print(f"  Unresolvable (no tile coverage / nodata pixel): {len(unresolvable)}")

    if disagree:
        print("\nDisagreements (existing value vs. what the raster resolves to now):")
        for entity_id, site_id, existing, resolved in disagree:
            print(f"  entity_id {entity_id} (site_id {site_id}): existing '{existing}' vs resolved '{resolved}'")

    if agree or disagree:
        rate = len(agree) / (len(agree) + len(disagree)) * 100
        print(f"\nAgreement rate (excluding unresolvable): {rate:.1f}%")


if __name__ == "__main__":
    main()
