#!/usr/bin/env python3
"""
Migrate published SISALv3 CSVs into a properly-typed SISALv3.1 SQLite database:
- Real types, PKs, FKs, CHECK constraints (translated from sisalv3_schema.dbml)
- New v3.1 provenance tables: database_release, code_artifact, person, entity_person, release_person
- entity gets added_in_release_id / last_modified_release_id
- Backfills a single v3.0 baseline database_release row
- Parses entity.contact (free text) into person + entity_person rows
- Leaves person.orcid NULL, code_artifact empty, release_person empty (per design: data_contributor
  is derived, not stored; the other four roles need a human to assign them)
Usage:
    python migrate_v3_to_v3.1.py <path-to-sisalv3-csv-folder> [output-dir]

<path-to-sisalv3-csv-folder> is the per-table CSV export of the published SISALv3
release (DOI 10.5287/ora-2nanwp4rk) — one file per table, e.g. site.csv, entity.csv,
sample.csv, etc. output-dir defaults to ./output relative to the current directory.
"""
import sqlite3, csv, os, re, sys
from pathlib import Path
from collections import OrderedDict

if len(sys.argv) < 2:
    sys.exit("Usage: python migrate_v3_to_v3.1.py <path-to-sisalv3-csv-folder> [output-dir]")

SRC_CSV = Path(sys.argv[1])
OUT_DIR = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("output")
DB_PATH = OUT_DIR / "sisalv3.1.db"

OUT_DIR.mkdir(parents=True, exist_ok=True)
if DB_PATH.exists():
    DB_PATH.unlink()

conn = sqlite3.connect(DB_PATH)
conn.execute("PRAGMA foreign_keys = ON")
cur = conn.cursor()

# ---------------------------------------------------------------- enum lists
YES_NO = ['yes', 'no']
YES_NO_UNKNOWN = ['yes', 'no', 'unknown']
YES_NO_OTHER_UNKNOWN = ['yes', 'no', 'other (see notes)', 'unknown']
DATE_TYPE = ['C14', 'MC-ICP-MS U/Th', 'ICP-MS U/Th Other', 'Alpha U/Th', 'TIMS',
             'U/Th unspecified', 'Cross-dating', 'Multiple methods', 'Event; hiatus',
             'Event; actively forming', 'Event; start of laminations',
             'Event; end of laminations', 'unknown', 'other (see notes)']
MATERIAL_DATED = ['calcite', 'aragonite', 'organic', 'mixed (see notes)', 'other (see notes)', 'unknown']
CALIB_USED = ['INTCAL13 NH', 'INTCAL13 SH', 'INTCAL13 marine', 'INTCAL09', 'INTCAL09 marine',
              'INTCAL04 NH', 'INTCAL04 SH', 'INTCAL98', 'FAIRBANKS09', 'not calibrated',
              'unknown', 'other (see notes)']
DECAY_CONSTANT = ['Cheng et al. 2000', 'Cheng et al. 2013', 'Edwards et al. 1987',
                   'Ivanovich & Harmon 1992', 'other (see notes)', 'unknown']
ENTITY_STATUS = ['current', 'superseded', 'current partially modified']
DEPTH_REF = ['from top', 'from base', 'not applicable']
GEOLOGY = ['limestone', 'dolomite', 'marble', 'dolomite limestone', 'marly limestone',
           'calcarenite', 'mixed (see notes)', 'other (see notes)', 'unknown']
ROCK_AGE = ['Holocene', 'Pleistocene', 'Pliocene', 'Miocene', 'Oligocene', 'Eocene',
            'Palaeocene', 'Cretaceous', 'Jurassic', 'Triassic', 'Permian', 'Carboniferous',
            'Devonian', 'Silurian', 'Ordovician', 'Cambrian', 'Precambrian',
            'mixed (see notes)', 'other (see notes)', 'unknown']
WOKAM = ['continuous carbonate', 'discontinuous carbonate', 'continuous evaporite',
         'discontinuous evaporite', 'mixed carbonate and evaporite']
VEGETATION_TYPE = ['evergreen', 'deciduous', 'shrubland', 'grassland', 'sparse', 'barren',
                    'trees and grass', 'trees and shrubs', 'moorland', 'mixed (see notes)',
                    'other (see notes)', 'unknown']
LAND_USE = ['water body', 'wetland', 'forest', 'farmland', 'pasture', 'concrete and built up',
            'limited or no use', 'mixed (see notes)', 'other (see notes)', 'unknown']
COPERNICUS_LCC = ['unknown', 'shrubs', 'herbaceous vegetation',
                   'cultivated and managed vegetation / agriculture', 'urban / built up',
                   'bare / sparse vegetation', 'snow and ice', 'permanent water bodies',
                   'herbaceous wetland', 'moss and lichen',
                   'closed forest, evergreen needle leaf', 'closed forest, evergreen broad leaf',
                   'closed forest, deciduous needle leaf', 'closed forest, deciduous broad leaf',
                   'closed forest, mixed', 'closed forest, other',
                   'open forest, evergreen needle leaf', 'open forest, evergreen broad leaf',
                   'open forest, deciduous needle leaf', 'open forest, deciduous broad leaf',
                   'open forest, mixed', 'open forest, other', 'ocean']
COVER_TYPE = ['thick', 'thin', 'patchy', 'barren', 'mixed (see notes)', 'other (see notes)', 'unknown']
SPELEOTHEM_TYPE = ['stalagmite', 'composite', 'stalactite', 'flowstone', 'mixed (see notes)',
                    'other (see notes)', 'unknown']
ISO_STD = ['PDB', 'Vienna-PDB', 'unknown']
PROXY_METHOD = ['solution ICP-MS', 'solution OES-MS', 'solution ICP-AES', 'synchroton XRF',
                 'micro XRF', 'micro XRF (ITRAX)', 'LA-ICP-MS', 'LA-ICP-AES', 'SIMS',
                 'Electron Microprobe (EPMA)', 'TIMS', 'other (see notes)', 'unknown']
PROXY_STD = ['NIST SRM 610', 'NIST SRM 612', 'NIST SRM 614', 'CaCO3 (NIST SRM 915a)',
             'SrCO3 (NIST SRM 987)', 'MACS-1', 'MACS-3',
             'standard stock solutions of known concentrations',
             'multi-element standards pre-calibrated by Merck etc.',
             'Japanese coral standard JCP-1 doped with single element standards of known concentrations',
             'metal standards', 'other (see notes)', 'unknown']
PROXY_DOWNSAMPLED = ['No-not applicable',
                       'No-aliquots of same powder used for stable isotope and trace element measurements',
                       'No-but different powders used for stable isotope and trace element measurements',
                       'Yes-author downsampled', 'Yes-SISAL downsampled', 'other (see notes)', 'unknown']
PROXY_DOWNSAMPLING_METHOD = ['running average/mean', 'linear interpolation',
                               'NESTool - kernel based downsampling',
                               'IgorPro - linear smoothing splines', 'IgorPro - interpolation',
                               'Binned average', 'Savitzky-Golay filter', 'Gaussian kernel',
                               'Loess', 'Lowess', 'MATLAB - interpolation', 'other (see notes)', 'unknown']
SAMPLE_MINERALOGY = ['calcite', 'secondary calcite', 'aragonite', 'vaterite', 'organic',
                       'mixed (see notes)', 'other (see notes)', 'unknown']
ARAG_CORR = ['yes', 'no', 'not applicable', 'unknown']
AGE_MODEL_TYPE = ['linear', 'linear between dates', 'polynomial fit',
                   'polynomial fit omitting outliers', 'Bayesian', 'Bayesian Bacon',
                   'Bayesian Bchron', 'StalAge', 'StalAge and other', 'Clam', 'COPRA', 'OxCal',
                   'mixed (see notes)', 'other (see notes)', 'unknown']
ANN_LAM_CHECK = ['14C peak', '14C slope', 'U/Th cycle', 'trace element cycle', 'assumed',
                   'not applicable', 'other (see notes)', 'unknown']
DEP_RATE_CHECK = ['yes', 'no', 'assumed', 'not applicable', 'unknown']
GAP_FLAG = ['G']
HIATUS_FLAG = ['H']
RELEASE_TYPE = ['official', 'beta']
ROLE = ['release_steward', 'data_curator', 'workflow_developer', 'project_lead', 'data_contributor']
ARTIFACT_TYPE = ['agemodel', 'downsampling']


def CK(col, values):
    quoted = ",".join("'" + v.replace("'", "''") + "'" for v in values)
    return f'"{col}" IS NULL OR "{col}" IN ({quoted})'


# ---------------------------------------------------------------- DDL
DDL = f"""
CREATE TABLE code_artifact (
  artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
  artifact_type TEXT CHECK ({CK('artifact_type', ARTIFACT_TYPE)}),
  doi TEXT,
  version_label TEXT,
  description TEXT
);

CREATE TABLE database_release (
  release_id INTEGER PRIMARY KEY AUTOINCREMENT,
  database_generation TEXT,
  release_version TEXT,
  release_date TEXT,
  release_type TEXT CHECK ({CK('release_type', RELEASE_TYPE)}),
  project TEXT,
  qc_version TEXT,
  sqlgen_version TEXT,
  agemodel_artifact_id INTEGER REFERENCES code_artifact(artifact_id) ON DELETE SET NULL ON UPDATE CASCADE,
  downsampling_artifact_id INTEGER REFERENCES code_artifact(artifact_id) ON DELETE SET NULL ON UPDATE CASCADE,
  release_doi TEXT,
  previous_release TEXT,
  release_notes TEXT
);

CREATE TABLE person (
  person_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  orcid TEXT
);

CREATE TABLE site (
  site_id INTEGER PRIMARY KEY,
  site_name TEXT,
  latitude REAL,
  longitude REAL,
  elevation REAL,
  monitoring TEXT CHECK ({CK('monitoring', YES_NO_UNKNOWN)})
);
CREATE INDEX idx_site_name ON site(site_name);

CREATE TABLE notes (
  site_id INTEGER PRIMARY KEY REFERENCES site(site_id) ON DELETE CASCADE ON UPDATE CASCADE,
  notes TEXT NOT NULL
);

CREATE TABLE entity (
  entity_id INTEGER PRIMARY KEY,
  site_id INTEGER REFERENCES site(site_id) ON DELETE CASCADE ON UPDATE CASCADE,
  entity_name TEXT,
  entity_status TEXT CHECK ({CK('entity_status', ENTITY_STATUS)}),
  corresponding_current TEXT,
  persist_id TEXT NOT NULL DEFAULT 'unset',
  depth_ref TEXT CHECK ({CK('depth_ref', DEPTH_REF)}),
  geology TEXT CHECK ({CK('geology', GEOLOGY)}),
  rock_age TEXT CHECK ({CK('rock_age', ROCK_AGE)}),
  wokam TEXT CHECK ({CK('wokam', WOKAM)}),
  vegetation_type TEXT CHECK ({CK('vegetation_type', VEGETATION_TYPE)}),
  land_use TEXT CHECK ({CK('land_use', LAND_USE)}),
  copernicus_lcc TEXT CHECK ({CK('copernicus_lcc', COPERNICUS_LCC)}),
  cover_type TEXT CHECK ({CK('cover_type', COVER_TYPE)}),
  cover_thickness REAL,
  host_rock_trace_elements TEXT CHECK ({CK('host_rock_trace_elements', YES_NO_OTHER_UNKNOWN)}),
  drip_water_trace_elements TEXT CHECK ({CK('drip_water_trace_elements', YES_NO_OTHER_UNKNOWN)}),
  distance_entrance REAL,
  speleothem_type TEXT CHECK ({CK('speleothem_type', SPELEOTHEM_TYPE)}),
  drip_type TEXT,
  drip_height REAL,
  d13C TEXT CHECK ({CK('d13C', YES_NO_UNKNOWN)}),
  d18O TEXT CHECK ({CK('d18O', YES_NO_UNKNOWN)}),
  iso_std TEXT CHECK ({CK('iso_std', ISO_STD)}),
  d18O_water_equilibrium TEXT CHECK ({CK('d18O_water_equilibrium', YES_NO_OTHER_UNKNOWN)}),
  d18O_dripwater_carbonate_difference REAL,
  organics TEXT CHECK ({CK('organics', YES_NO_OTHER_UNKNOWN)}),
  fluid_inclusions TEXT CHECK ({CK('fluid_inclusions', YES_NO_OTHER_UNKNOWN)}),
  mineralogy_petrology_fabric TEXT CHECK ({CK('mineralogy_petrology_fabric', YES_NO_OTHER_UNKNOWN)}),
  clumped_isotopes TEXT CHECK ({CK('clumped_isotopes', YES_NO_OTHER_UNKNOWN)}),
  noble_gas_temperatures TEXT CHECK ({CK('noble_gas_temperatures', YES_NO_OTHER_UNKNOWN)}),
  C14 TEXT CHECK ({CK('C14', YES_NO_OTHER_UNKNOWN)}),
  ODL TEXT CHECK ({CK('ODL', YES_NO_OTHER_UNKNOWN)}),
  Sr_Ca TEXT CHECK ({CK('Sr_Ca', YES_NO_OTHER_UNKNOWN)}),
  Sr_Ca_method TEXT CHECK ({CK('Sr_Ca_method', PROXY_METHOD)}),
  Sr_Ca_std TEXT CHECK ({CK('Sr_Ca_std', PROXY_STD)}),
  Sr_Ca_downsampled TEXT CHECK ({CK('Sr_Ca_downsampled', PROXY_DOWNSAMPLED)}),
  Sr_Ca_downsampling_method TEXT CHECK ({CK('Sr_Ca_downsampling_method', PROXY_DOWNSAMPLING_METHOD)}),
  Mg_Ca TEXT CHECK ({CK('Mg_Ca', YES_NO_OTHER_UNKNOWN)}),
  Mg_Ca_method TEXT CHECK ({CK('Mg_Ca_method', PROXY_METHOD)}),
  Mg_Ca_std TEXT CHECK ({CK('Mg_Ca_std', PROXY_STD)}),
  Mg_Ca_downsampled TEXT CHECK ({CK('Mg_Ca_downsampled', PROXY_DOWNSAMPLED)}),
  Mg_Ca_downsampling_method TEXT CHECK ({CK('Mg_Ca_downsampling_method', PROXY_DOWNSAMPLING_METHOD)}),
  Ba_Ca TEXT CHECK ({CK('Ba_Ca', YES_NO_OTHER_UNKNOWN)}),
  Ba_Ca_method TEXT CHECK ({CK('Ba_Ca_method', PROXY_METHOD)}),
  Ba_Ca_std TEXT CHECK ({CK('Ba_Ca_std', PROXY_STD)}),
  Ba_Ca_downsampled TEXT CHECK ({CK('Ba_Ca_downsampled', PROXY_DOWNSAMPLED)}),
  Ba_Ca_downsampling_method TEXT CHECK ({CK('Ba_Ca_downsampling_method', PROXY_DOWNSAMPLING_METHOD)}),
  U_Ca TEXT CHECK ({CK('U_Ca', YES_NO_OTHER_UNKNOWN)}),
  U_Ca_method TEXT CHECK ({CK('U_Ca_method', PROXY_METHOD)}),
  U_Ca_std TEXT CHECK ({CK('U_Ca_std', PROXY_STD)}),
  U_Ca_downsampled TEXT CHECK ({CK('U_Ca_downsampled', PROXY_DOWNSAMPLED)}),
  U_Ca_downsampling_method TEXT CHECK ({CK('U_Ca_downsampling_method', PROXY_DOWNSAMPLING_METHOD)}),
  P_Ca TEXT CHECK ({CK('P_Ca', YES_NO_OTHER_UNKNOWN)}),
  P_Ca_method TEXT CHECK ({CK('P_Ca_method', PROXY_METHOD)}),
  P_Ca_std TEXT CHECK ({CK('P_Ca_std', PROXY_STD)}),
  P_Ca_downsampled TEXT CHECK ({CK('P_Ca_downsampled', PROXY_DOWNSAMPLED)}),
  P_Ca_downsampling_method TEXT CHECK ({CK('P_Ca_downsampling_method', PROXY_DOWNSAMPLING_METHOD)}),
  Sr_isotopes TEXT CHECK ({CK('Sr_isotopes', YES_NO_OTHER_UNKNOWN)}),
  Sr_isotopes_method TEXT CHECK ({CK('Sr_isotopes_method', PROXY_METHOD)}),
  Sr_isotopes_std TEXT CHECK ({CK('Sr_isotopes_std', PROXY_STD)}),
  trace_elements_datafile TEXT CHECK ({CK('trace_elements_datafile', YES_NO)}),
  trace_elements_metadatafile TEXT CHECK ({CK('trace_elements_metadatafile', YES_NO)}),
  cave_map TEXT CHECK ({CK('cave_map', YES_NO)}),
  entity_scan TEXT CHECK ({CK('entity_scan', YES_NO)}),
  added_in_release_id INTEGER REFERENCES database_release(release_id) ON DELETE SET NULL ON UPDATE CASCADE,
  last_modified_release_id INTEGER REFERENCES database_release(release_id) ON DELETE SET NULL ON UPDATE CASCADE,
  data_DOI_URL TEXT
);
CREATE INDEX idx_entity_site ON entity(site_id);
CREATE INDEX idx_entity_added_release ON entity(added_in_release_id);
CREATE INDEX idx_entity_modified_release ON entity(last_modified_release_id);

CREATE TABLE entity_person (
  entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  person_id INTEGER REFERENCES person(person_id) ON DELETE CASCADE ON UPDATE CASCADE,
  PRIMARY KEY (entity_id, person_id)
);

CREATE TABLE release_person (
  release_id INTEGER REFERENCES database_release(release_id) ON DELETE CASCADE ON UPDATE CASCADE,
  person_id INTEGER REFERENCES person(person_id) ON DELETE CASCADE ON UPDATE CASCADE,
  role TEXT CHECK ({CK('role', ROLE)}),
  PRIMARY KEY (release_id, person_id, role)
);

CREATE TABLE composite_link_entity (
  composite_entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  single_entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_cle_composite ON composite_link_entity(composite_entity_id);
CREATE INDEX idx_cle_single ON composite_link_entity(single_entity_id);

CREATE TABLE reference (
  ref_id INTEGER PRIMARY KEY,
  citation TEXT,
  publication_DOI TEXT
);

CREATE TABLE entity_link_reference (
  entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  ref_id INTEGER REFERENCES reference(ref_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_elr_entity ON entity_link_reference(entity_id);
CREATE INDEX idx_elr_ref ON entity_link_reference(ref_id);

CREATE TABLE dating (
  dating_id INTEGER PRIMARY KEY,
  entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  date_type TEXT CHECK ({CK('date_type', DATE_TYPE)}),
  depth_dating REAL,
  dating_thickness REAL,
  lab_num TEXT,
  material_dated TEXT CHECK ({CK('material_dated', MATERIAL_DATED)}),
  min_weight REAL,
  max_weight REAL,
  uncorr_age REAL,
  uncorr_age_uncert_pos REAL,
  uncorr_age_uncert_neg REAL,
  "14C_correction" REAL,
  calib_used TEXT CHECK ({CK('calib_used', CALIB_USED)}),
  date_used TEXT CHECK ({CK('date_used', YES_NO_UNKNOWN)}),
  "238U_content" REAL,
  "238U_uncertainty" REAL,
  "232Th_content" REAL,
  "232Th_uncertainty" REAL,
  "230Th_content" REAL,
  "230Th_uncertainty" REAL,
  "230Th_232Th_ratio" REAL,
  "230Th_232Th_ratio_uncertainty" REAL,
  "230Th_238U_activity" REAL,
  "230Th_238U_activity_uncertainty" REAL,
  "234U_238U_activity" REAL,
  "234U_238U_activity_uncertainty" REAL,
  ini_230Th_232Th_ratio REAL,
  ini_230Th_232Th_ratio_uncertainty REAL,
  decay_constant TEXT CHECK ({CK('decay_constant', DECAY_CONSTANT)}),
  corr_age REAL,
  corr_age_uncert_pos REAL,
  corr_age_uncert_neg REAL,
  date_used_lin_interp TEXT CHECK ({CK('date_used_lin_interp', YES_NO)}),
  date_used_lin_reg TEXT CHECK ({CK('date_used_lin_reg', YES_NO)}),
  date_used_Bchron TEXT CHECK ({CK('date_used_Bchron', YES_NO)}),
  date_used_Bacon TEXT CHECK ({CK('date_used_Bacon', YES_NO)}),
  date_used_OxCal TEXT CHECK ({CK('date_used_OxCal', YES_NO)}),
  date_used_copRa TEXT CHECK ({CK('date_used_copRa', YES_NO)}),
  date_used_StalAge TEXT CHECK ({CK('date_used_StalAge', YES_NO)})
);
CREATE INDEX idx_dating_entity ON dating(entity_id);

CREATE TABLE dating_lamina (
  dating_lamina_id INTEGER PRIMARY KEY,
  entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  depth_lam REAL,
  lam_thickness REAL,
  lam_age REAL NOT NULL,
  lam_age_uncert_pos REAL,
  lam_age_uncert_neg REAL
);
CREATE INDEX idx_dl_entity ON dating_lamina(entity_id);

CREATE TABLE sample (
  sample_id INTEGER PRIMARY KEY,
  entity_id INTEGER REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  sample_thickness REAL,
  depth_sample REAL,
  mineralogy TEXT CHECK ({CK('mineralogy', SAMPLE_MINERALOGY)}),
  arag_corr TEXT CHECK ({CK('arag_corr', ARAG_CORR)})
);
CREATE INDEX idx_sample_entity ON sample(entity_id);

CREATE TABLE original_chronology (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  interp_age REAL,
  interp_age_uncert_pos REAL,
  interp_age_uncert_neg REAL,
  age_model_type TEXT CHECK ({CK('age_model_type', AGE_MODEL_TYPE)}),
  ann_lam_check TEXT CHECK ({CK('ann_lam_check', ANN_LAM_CHECK)}),
  dep_rate_check TEXT CHECK ({CK('dep_rate_check', DEP_RATE_CHECK)})
);

CREATE TABLE sisal_chronology (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  lin_interp_age REAL, lin_interp_age_uncert_pos REAL, lin_interp_age_uncert_neg REAL,
  lin_reg_age REAL, lin_reg_age_uncert_pos REAL, lin_reg_age_uncert_neg REAL,
  Bchron_age REAL, Bchron_age_uncert_pos REAL, Bchron_age_uncert_neg REAL,
  Bacon_age REAL, Bacon_age_uncert_pos REAL, Bacon_age_uncert_neg REAL,
  OxCal_age REAL, OxCal_age_uncert_pos REAL, OxCal_age_uncert_neg REAL,
  copRa_age REAL, copRa_age_uncert_pos REAL, copRa_age_uncert_neg REAL,
  StalAge_age REAL, StalAge_age_uncert_pos REAL, StalAge_age_uncert_neg REAL
);

CREATE TABLE gap (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  gap TEXT CHECK ({CK('gap', GAP_FLAG)})
);

CREATE TABLE hiatus (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  hiatus TEXT CHECK ({CK('hiatus', HIATUS_FLAG)})
);

CREATE TABLE d13C (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  d13C_measurement REAL, d13C_precision REAL
);
CREATE TABLE d18O (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  d18O_measurement REAL, d18O_precision REAL
);
CREATE TABLE Sr_Ca (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  Sr_Ca_measurement REAL, Sr_Ca_precision REAL
);
CREATE TABLE Mg_Ca (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  Mg_Ca_measurement REAL, Mg_Ca_precision REAL
);
CREATE TABLE Ba_Ca (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  Ba_Ca_measurement REAL, Ba_Ca_precision REAL
);
CREATE TABLE U_Ca (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  U_Ca_measurement REAL, U_Ca_precision REAL
);
CREATE TABLE P_Ca (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  P_Ca_measurement REAL, P_Ca_precision REAL
);
CREATE TABLE Sr_isotopes (
  sample_id INTEGER PRIMARY KEY REFERENCES sample(sample_id) ON DELETE CASCADE ON UPDATE CASCADE,
  Sr_isotopes_measurement REAL, Sr_isotopes_precision REAL
);
"""

cur.executescript(DDL)
conn.commit()
print("Schema created: OK")

# ---------------------------------------------------------------- helpers
def to_int(v):
    if v is None or v == '' or v == 'NA':
        return None
    return int(float(v)) if '.' in v else int(v)

def to_float(v):
    if v is None or v == '' or v == 'NA':
        return None
    return float(v)

def to_text(v):
    if v is None or v == '' or v == 'NA':
        return None
    return v

def load_csv(table, columns, casts, filename=None):
    """columns: list of DB column names in insertion order.
       casts: dict colname -> cast fn (default to_text)."""
    fn = filename or f"{table}.csv"
    path = SRC_CSV / fn
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        r = csv.DictReader(f)
        for row in r:
            vals = []
            for c in columns:
                cast = casts.get(c, to_text)
                vals.append(cast(row.get(c)))
            rows.append(tuple(vals))
    placeholders = ",".join("?" * len(columns))
    colnames = ",".join(f'"{c}"' for c in columns)
    cur.executemany(f'INSERT INTO "{table}" ({colnames}) VALUES ({placeholders})', rows)
    conn.commit()
    print(f"  {table}: {len(rows)} rows loaded")

INT = to_int
FLOAT = to_float
TXT = to_text

# ---------------------------------------------------------------- load core tables (dependency order)
print("Loading published SISALv3 CSVs...")

load_csv('site', ['site_id','site_name','latitude','longitude','elevation','monitoring'],
         {'site_id': INT, 'latitude': FLOAT, 'longitude': FLOAT, 'elevation': FLOAT})

load_csv('notes', ['site_id','notes'], {'site_id': INT})

entity_cols = ['site_id','entity_id','entity_name','entity_status','corresponding_current',
    'persist_id','depth_ref','geology','rock_age','wokam','vegetation_type','land_use',
    'copernicus_lcc','cover_type','cover_thickness','host_rock_trace_elements',
    'drip_water_trace_elements','distance_entrance','speleothem_type','drip_type','drip_height',
    'd13C','d18O','iso_std','d18O_water_equilibrium','d18O_dripwater_carbonate_difference',
    'organics','fluid_inclusions','mineralogy_petrology_fabric','clumped_isotopes',
    'noble_gas_temperatures','C14','ODL','Sr_Ca','Sr_Ca_method','Sr_Ca_std','Sr_Ca_downsampled',
    'Sr_Ca_downsampling_method','Mg_Ca','Mg_Ca_method','Mg_Ca_std','Mg_Ca_downsampled',
    'Mg_Ca_downsampling_method','Ba_Ca','Ba_Ca_method','Ba_Ca_std','Ba_Ca_downsampled',
    'Ba_Ca_downsampling_method','U_Ca','U_Ca_method','U_Ca_std','U_Ca_downsampled',
    'U_Ca_downsampling_method','P_Ca','P_Ca_method','P_Ca_std','P_Ca_downsampled',
    'P_Ca_downsampling_method','Sr_isotopes','Sr_isotopes_method','Sr_isotopes_std',
    'trace_elements_datafile','trace_elements_metadatafile','cave_map','entity_scan',
    'contact','data_DOI_URL']
entity_casts = {'site_id': INT, 'entity_id': INT,
                'cover_thickness': FLOAT, 'distance_entrance': FLOAT, 'drip_height': FLOAT,
                'd18O_dripwater_carbonate_difference': FLOAT}
# load into entity table but WITHOUT contact (handled separately via entity_person) and
# WITHOUT added_in_release_id/last_modified_release_id (backfilled after database_release exists)
entity_db_cols = [c for c in entity_cols if c not in ('contact',)]
rows = []
contact_by_entity = {}
with open(SRC_CSV / 'entity.csv', newline='', encoding='utf-8') as f:
    r = csv.DictReader(f)
    for row in r:
        vals = []
        for c in entity_db_cols:
            cast = entity_casts.get(c, TXT)
            vals.append(cast(row.get(c)))
        rows.append(tuple(vals))
        contact_by_entity[int(row['entity_id'])] = row.get('contact')
placeholders = ",".join("?" * len(entity_db_cols))
colnames = ",".join(f'"{c}"' for c in entity_db_cols)
cur.executemany(f'INSERT INTO entity ({colnames}) VALUES ({placeholders})', rows)
conn.commit()
print(f"  entity: {len(rows)} rows loaded (contact deferred to entity_person)")

load_csv('composite_link_entity', ['composite_entity_id','single_entity_id'],
         {'composite_entity_id': INT, 'single_entity_id': INT})

load_csv('reference', ['ref_id','citation','publication_DOI'], {'ref_id': INT})

load_csv('entity_link_reference', ['entity_id','ref_id'], {'entity_id': INT, 'ref_id': INT})

dating_cols = ['dating_id','entity_id','date_type','depth_dating','dating_thickness','lab_num',
    'material_dated','min_weight','max_weight','uncorr_age','uncorr_age_uncert_pos',
    'uncorr_age_uncert_neg','14C_correction','calib_used','date_used','238U_content',
    '238U_uncertainty','232Th_content','232Th_uncertainty','230Th_content','230Th_uncertainty',
    '230Th_232Th_ratio','230Th_232Th_ratio_uncertainty','230Th_238U_activity',
    '230Th_238U_activity_uncertainty','234U_238U_activity','234U_238U_activity_uncertainty',
    'ini_230Th_232Th_ratio','ini_230Th_232Th_ratio_uncertainty','decay_constant','corr_age',
    'corr_age_uncert_pos','corr_age_uncert_neg','date_used_lin_interp','date_used_lin_reg',
    'date_used_Bchron','date_used_Bacon','date_used_OxCal','date_used_copRa','date_used_StalAge']
dating_casts = {'dating_id': INT, 'entity_id': INT}
for c in dating_cols:
    if c not in ('dating_id','entity_id') and not c.startswith(('date_type','material_dated',
        'calib_used','date_used','decay_constant')) and c not in ('lab_num',):
        dating_casts[c] = FLOAT
load_csv('dating', dating_cols, dating_casts)

load_csv('dating_lamina', ['dating_lamina_id','entity_id','depth_lam','lam_thickness','lam_age',
    'lam_age_uncert_pos','lam_age_uncert_neg'],
    {'dating_lamina_id': INT, 'entity_id': INT, 'depth_lam': FLOAT, 'lam_thickness': FLOAT,
     'lam_age': FLOAT, 'lam_age_uncert_pos': FLOAT, 'lam_age_uncert_neg': FLOAT})

load_csv('sample', ['entity_id','sample_id','sample_thickness','depth_sample','mineralogy','arag_corr'],
    {'entity_id': INT, 'sample_id': INT, 'sample_thickness': FLOAT, 'depth_sample': FLOAT})

load_csv('original_chronology', ['sample_id','interp_age','interp_age_uncert_pos',
    'interp_age_uncert_neg','age_model_type','ann_lam_check','dep_rate_check'],
    {'sample_id': INT, 'interp_age': FLOAT, 'interp_age_uncert_pos': FLOAT, 'interp_age_uncert_neg': FLOAT})

sc_cols = ['sample_id','lin_interp_age','lin_interp_age_uncert_pos','lin_interp_age_uncert_neg',
    'lin_reg_age','lin_reg_age_uncert_pos','lin_reg_age_uncert_neg','Bchron_age',
    'Bchron_age_uncert_pos','Bchron_age_uncert_neg','Bacon_age','Bacon_age_uncert_pos',
    'Bacon_age_uncert_neg','OxCal_age','OxCal_age_uncert_pos','OxCal_age_uncert_neg',
    'copRa_age','copRa_age_uncert_pos','copRa_age_uncert_neg','StalAge_age',
    'StalAge_age_uncert_pos','StalAge_age_uncert_neg']
sc_casts = {c: (INT if c == 'sample_id' else FLOAT) for c in sc_cols}
load_csv('sisal_chronology', sc_cols, sc_casts)

load_csv('gap', ['sample_id','gap'], {'sample_id': INT})
load_csv('hiatus', ['sample_id','hiatus'], {'sample_id': INT})

for t, mcol in [('d13C','d13C'), ('d18O','d18O'), ('Sr_Ca','Sr_Ca'), ('Mg_Ca','Mg_Ca'),
                ('Ba_Ca','Ba_Ca'), ('U_Ca','U_Ca'), ('P_Ca','P_Ca'), ('Sr_isotopes','Sr_isotopes')]:
    cols = ['sample_id', f'{mcol}_measurement', f'{mcol}_precision']
    load_csv(t, cols, {'sample_id': INT, f'{mcol}_measurement': FLOAT, f'{mcol}_precision': FLOAT})

print("\nAll 21 published tables loaded.\n")

# ---------------------------------------------------------------- v3.0 baseline release
cur.execute("""INSERT INTO database_release
    (database_generation, release_version, release_type, release_notes, release_doi)
    VALUES ('v3', 'v3.0', 'official',
    'Baseline backfill row for the published SISALv3 release, created during the v3.1 migration on 2026-08-20. release_date/release_doi left for Laura to fill in from the actual publication record (DOI 10.5287/ora-2nanwp4rk referenced in sisalv3_db_reference.md, exact release date not found in local files).',
    NULL)""")
conn.commit()
v3_release_id = cur.execute("SELECT release_id FROM database_release WHERE release_version='v3.0'").fetchone()[0]
print(f"v3.0 baseline database_release row created: release_id={v3_release_id}")

# ---------------------------------------------------------------- parse entity.contact -> person / entity_person
SEP_RE = re.compile(r'\s*[,;/]\s*|\s+and\s+|\s+&\s+')

def split_names(raw):
    if not raw or raw in ('NA', ''):
        return []
    parts = [p.strip() for p in SEP_RE.split(raw)]
    return [p for p in parts if p]

person_id_by_name = {}
entity_person_rows = []
all_names_seen = OrderedDict()

for entity_id, raw in contact_by_entity.items():
    names = split_names(raw)
    for n in names:
        all_names_seen[n] = all_names_seen.get(n, 0) + 1
        if n not in person_id_by_name:
            cur.execute("INSERT INTO person (name) VALUES (?)", (n,))
            person_id_by_name[n] = cur.lastrowid
        entity_person_rows.append((entity_id, person_id_by_name[n]))

conn.commit()
cur.executemany("INSERT OR IGNORE INTO entity_person (entity_id, person_id) VALUES (?, ?)",
                 entity_person_rows)
conn.commit()
print(f"person: {len(person_id_by_name)} distinct individuals created from {len(contact_by_entity)} entity.contact values")
print(f"entity_person: {len(entity_person_rows)} link rows created")

# fuzzy-duplicate flagging (NOT auto-merged — for human review only)
import difflib
names_list = list(all_names_seen.keys())
near_dupes = []
seen_pairs = set()
for i, a in enumerate(names_list):
    for b in names_list[i+1:]:
        ratio = difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()
        if ratio >= 0.90:
            key = tuple(sorted((a, b)))
            if key not in seen_pairs:
                seen_pairs.add(key)
                near_dupes.append((a, b, round(ratio, 3)))
near_dupes.sort(key=lambda x: -x[2])

# ---------------------------------------------------------------- backfill entity.added_in_release_id / last_modified_release_id
cur.execute("UPDATE entity SET added_in_release_id = ?, last_modified_release_id = ?",
            (v3_release_id, v3_release_id))
conn.commit()
n_updated = cur.execute("SELECT COUNT(*) FROM entity WHERE added_in_release_id = ?", (v3_release_id,)).fetchone()[0]
print(f"entity.added_in_release_id / last_modified_release_id backfilled to v3.0 for {n_updated} entities")

# ---------------------------------------------------------------- verification
print("\n--- Verification: row counts ---")
tables = ['site','notes','entity','composite_link_entity','reference','entity_link_reference',
          'dating','dating_lamina','sample','original_chronology','sisal_chronology','gap',
          'hiatus','d13C','d18O','Sr_Ca','Mg_Ca','Ba_Ca','U_Ca','P_Ca','Sr_isotopes',
          'database_release','code_artifact','person','entity_person','release_person']
counts = {}
for t in tables:
    n = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    counts[t] = n
    print(f"  {t}: {n}")

print("\n--- Spot check: entities 202, 33, 212 (from sisalv3_db_reference.md) ---")
for eid in (202, 33, 212):
    row = cur.execute("""SELECT e.entity_id, e.entity_name, s.site_name, s.latitude, s.longitude, s.elevation
                          FROM entity e JOIN site s ON e.site_id = s.site_id WHERE e.entity_id = ?""", (eid,)).fetchone()
    print(f"  {row}")

print("\n--- FK integrity check (should be empty) ---")
fk_errors = cur.execute("PRAGMA foreign_key_check").fetchall()
print(f"  {len(fk_errors)} foreign key violations" if fk_errors else "  0 foreign key violations")
if fk_errors:
    for e in fk_errors[:20]:
        print("   ", e)

conn.close()

# ---------------------------------------------------------------- write reports
report_path = OUT_DIR / "migration_log.txt"
with open(report_path, 'w') as f:
    f.write("SISALv3 -> SISALv3.1 migration log\n")
    f.write("Run: 2026-08-20\n\n")
    f.write("Row counts:\n")
    for t, n in counts.items():
        f.write(f"  {t}: {n}\n")
    f.write(f"\nPerson records created: {len(person_id_by_name)}\n")
    f.write(f"Entity-person link rows: {len(entity_person_rows)}\n")
    f.write(f"Foreign key violations: {len(fk_errors)}\n")
    f.write(f"\nNear-duplicate name pairs flagged for manual review (similarity >= 0.90, NOT auto-merged):\n")
    for a, b, r in near_dupes:
        f.write(f"  {r}  {a!r}  <->  {b!r}\n")

print(f"\nMigration log written to {report_path}")
print(f"\n{len(near_dupes)} near-duplicate name pairs flagged (see log) — NOT auto-merged.")
for a, b, r in near_dupes[:30]:
    print(f"  {r}  {a!r} <-> {b!r}")
