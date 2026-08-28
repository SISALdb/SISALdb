#!/usr/bin/env python3
"""
Build the SISALv3.1 SQLite database from the CSVs checked into this repo (csv/).

Self-contained: this repo carries the full SISALv3.1 data as diff-friendly CSVs,
so no external SISALv3 export is needed to build the database. Schema (types,
primary keys, foreign keys, CHECK constraints for the controlled vocabularies)
comes from schema/schema.dbml, reproduced here as SQLite DDL.

Usage:
    python3 scripts/build_db.py [output-dir]

output-dir defaults to ./output.
"""
import sqlite3, csv, sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = REPO_ROOT / "csv"
OUT_DIR = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("output")
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
PROJECT_TYPE = ['active', 'closed']
ROLE = ['release_steward', 'data_curator', 'workflow_developer', 'project_lead', 'data_contributor']
ARTIFACT_TYPE = ['agemodel', 'downsampling', 'copernicus_lcc']


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
  qc_version TEXT,
  sqlgen_version TEXT,
  agemodel_artifact_id INTEGER REFERENCES code_artifact(artifact_id) ON DELETE SET NULL ON UPDATE CASCADE,
  downsampling_artifact_id INTEGER REFERENCES code_artifact(artifact_id) ON DELETE SET NULL ON UPDATE CASCADE,
  lcc_artifact_id INTEGER REFERENCES code_artifact(artifact_id) ON DELETE SET NULL ON UPDATE CASCADE,
  release_doi TEXT,
  previous_release INTEGER REFERENCES database_release(release_id) ON DELETE SET NULL ON UPDATE CASCADE,
  release_notes TEXT
  -- OPEN ISSUE (flagged, not fixed here): no project_id FK to `projects` below.
);

CREATE TABLE person (
  person_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  orcid TEXT
);

CREATE TABLE projects (
  project_id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_name TEXT,
  project_doi TEXT,
  project_status TEXT CHECK ({CK('project_status', PROJECT_TYPE)}),
  project_notes TEXT
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
  site_id INTEGER NOT NULL REFERENCES site(site_id) ON DELETE CASCADE ON UPDATE CASCADE,
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

CREATE TABLE entity_link_person (
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

CREATE TABLE project_person (
  project_id INTEGER REFERENCES projects(project_id) ON DELETE CASCADE ON UPDATE CASCADE,
  person_id INTEGER REFERENCES person(person_id) ON DELETE CASCADE ON UPDATE CASCADE,
  role TEXT CHECK ({CK('role', ROLE)}),
  PRIMARY KEY (project_id, person_id, role)
);

CREATE TABLE project_link_entity (
  project_id INTEGER NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE ON UPDATE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  PRIMARY KEY (project_id, entity_id)
);

CREATE TABLE composite_link_entity (
  composite_entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  single_entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_cle_composite ON composite_link_entity(composite_entity_id);
CREATE INDEX idx_cle_single ON composite_link_entity(single_entity_id);

CREATE TABLE reference (
  ref_id INTEGER PRIMARY KEY,
  citation TEXT,
  publication_DOI TEXT
);

CREATE TABLE entity_link_reference (
  entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  ref_id INTEGER NOT NULL REFERENCES reference(ref_id) ON DELETE CASCADE ON UPDATE CASCADE
);
CREATE INDEX idx_elr_entity ON entity_link_reference(entity_id);
CREATE INDEX idx_elr_ref ON entity_link_reference(ref_id);

CREATE TABLE dating (
  dating_id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
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
  entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
  depth_lam REAL,
  lam_thickness REAL,
  lam_age REAL NOT NULL,
  lam_age_uncert_pos REAL,
  lam_age_uncert_neg REAL
);
CREATE INDEX idx_dl_entity ON dating_lamina(entity_id);

CREATE TABLE sample (
  sample_id INTEGER PRIMARY KEY,
  entity_id INTEGER NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE ON UPDATE CASCADE,
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

# ---------------------------------------------------------------- generic CSV loader
# Load order matters for FK enforcement: parents before children.
LOAD_ORDER = [
    'code_artifact', 'database_release', 'person', 'projects', 'site', 'notes', 'entity',
    'entity_link_person', 'release_person', 'project_person', 'project_link_entity',
    'composite_link_entity', 'reference', 'entity_link_reference', 'dating', 'dating_lamina', 'sample',
    'original_chronology', 'sisal_chronology', 'gap', 'hiatus',
    'd13C', 'd18O', 'Sr_Ca', 'Mg_Ca', 'Ba_Ca', 'U_Ca', 'P_Ca', 'Sr_isotopes',
]

def cast_value(v, decl_type):
    if v is None or v == '':
        return None
    decl_type = (decl_type or '').upper()
    if 'INT' in decl_type:
        return int(v)
    if 'REAL' in decl_type or 'FLOA' in decl_type or 'DOUB' in decl_type:
        return float(v)
    return v

print("\nLoading CSVs...")
for table in LOAD_ORDER:
    csv_path = CSV_DIR / f"{table}.csv"
    col_info = cur.execute(f'PRAGMA table_info("{table}")').fetchall()
    col_types = {row[1]: row[2] for row in col_info}  # name -> declared type

    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        rows = [tuple(cast_value(row[c], col_types.get(c)) for c in columns) for row in reader]

    placeholders = ",".join("?" * len(columns))
    colnames = ",".join(f'"{c}"' for c in columns)
    cur.executemany(f'INSERT INTO "{table}" ({colnames}) VALUES ({placeholders})', rows)
    conn.commit()
    print(f"  {table}: {len(rows)} rows loaded")

print("\nAll tables loaded.")

# ---------------------------------------------------------------- verification
print("\n--- FK integrity check (should be empty) ---")
fk_errors = cur.execute("PRAGMA foreign_key_check").fetchall()
print(f"  {len(fk_errors)} foreign key violations" if fk_errors else "  0 foreign key violations")
if fk_errors:
    for e in fk_errors[:20]:
        print("   ", e)

conn.close()
print(f"\nDatabase built at {DB_PATH}")
