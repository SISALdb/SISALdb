# Reference data for `get_copernicus_lcc.py`

Local raster tiles that `get_copernicus_lcc.py` samples per-site. Git-ignored
(too large, not ours to redistribute) — this README is the exception.

## Copernicus Global Land Cover (CGLS-LC100, Collection 3)

1. Go to the viewer/download portal: <https://lcviewer.vito.be/>
2. The product ships as 20°×20° GeoTIFF tiles (100 m resolution, discrete
   classification), not one global file -- pick and download only the
   tile(s) that cover SISAL's actual site locations (see `csv/site.csv` for
   the full list of lat/lon). Most site clusters fit into a handful of
   tiles, not the whole grid.
3. Unzip/place the `.tif` file(s) directly into this folder (any filename
   is fine -- `get_copernicus_lcc.py` finds every `*.tif`/`*.tiff` here and
   uses whichever tile's bounds actually contain a given site).

If you run the script and it reports entities with "no raster tile
coverage," it prints the (lat, lon) of each -- download the tile(s)
covering those coordinates and rerun; no need to have every tile up front.

Year/version: pick the most recent discrete-classification epoch on the
portal unless a specific SISAL release calls for matching an earlier year.
