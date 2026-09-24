# Reference data for `get_wokam.py`

This folder holds the local geospatial reference dataset that `get_wokam.py`
looks up sites against. It's git-ignored (the shapefile is too large / not
ours to redistribute) — this README is the exception, so the folder itself
and the download step are documented in the repo.

## WOKAM (World Karst Aquifer Map)

1. Go to the BGR/WHYMAP Geoportal: <https://www.whymap.org/whymap/EN/Maps_Data/Wokam/wokam_node_en.html>
2. Download the WOKAM shapefile package (1:25,000,000 scale, free, no auth
   required).
3. Unzip it directly into this folder, so you end up with something like:
   ```
   data/
     whymap_wokam__v1.shp
     whymap_wokam__v1.dbf
     whymap_wokam__v1.shx
     whymap_wokam__v1.prj
     ...
   ```
   `get_wokam.py` picks up the first `*.shp` file it finds here — no
   renaming needed, just unzip and go.

That's it — `get_wokam.py` does the rest (point-in-polygon lookup per site,
backfill into `csv/entity.csv`).
