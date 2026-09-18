"""
Paths and constants for the SISAL DB update pipeline.
Edit DB_PATH and CSV_DIR to point to your local copies.
"""
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).parent

# SQLite database — dev copy for v4 update work
# Production source: /Users/lendres/SISAL-Agent/sisalv3.db
DB_PATH = Path(
    "/Users/lendres/Documents/ResearchHome/00_Researchtopics/Working Groups/"
    "AB_SISAL/SISAL-Neo cont./SQL_and_AgeModel/sisalv4_update_dev/sisalv4_dev.db"
)

# CSV export folder — lives alongside the dev DB, never touches the published sisalv3 folder
CSV_DIR = Path(
    "/Users/lendres/Documents/ResearchHome/00_Researchtopics/Working Groups/"
    "AB_SISAL/SISAL-Neo cont./SQL_and_AgeModel/sisalv4_update_dev/sisalv4_csv"
)

# ── Workbook sheet → DB table mapping ────────────────────────────────────────
# Sheets handled so far; extend as more tables are implemented.
SHEET_TO_TABLE = {
    "Site metadata":    "site",
    "Entity metadata":  "entity",
    # "Dating information": "dating",       # TODO next
    # "Sample data":        "sample/proxy", # TODO later
}

# Entity metadata columns present in workbook that map directly to DB columns.
# Columns in the workbook that do NOT exist in the DB are silently dropped.
ENTITY_WORKBOOK_COLS = [
    "entity_name", "geology", "rock_age", "vegetation_type", "land_use",
    "cover_type", "cover_thickness", "host_rock_trace_elements",
    "drip_water_trace_elements", "distance_entrance", "speleothem_type",
    "drip_type", "drip_height", "d13C", "d18O", "iso_std",
    "d18O_water_equilibrium", "d18O_dripwater_carbonate_difference",
    "organics", "fluid_inclusions", "mineralogy_petrology_fabric",
    "clumped_isotopes", "noble_gas_temperatures", "C14", "ODL",
    "Sr_Ca", "Sr_Ca_method", "Sr_Ca_std", "Sr_Ca_downsampled",
    "Sr_Ca_downsampling_method", "Mg_Ca", "Mg_Ca_method", "Mg_Ca_std",
    "Mg_Ca_downsampled", "Mg_Ca_downsampling_method", "Ba_Ca", "Ba_Ca_method",
    "Ba_Ca_std", "Ba_Ca_downsampled", "Ba_Ca_downsampling_method",
    "U_Ca", "U_Ca_method", "U_Ca_std", "U_Ca_downsampled",
    "U_Ca_downsampling_method", "P_Ca", "P_Ca_method", "P_Ca_std",
    "P_Ca_downsampled", "P_Ca_downsampling_method", "Sr_isotopes",
    "Sr_isotopes_method", "Sr_isotopes_std", "trace_elements_datafile",
    "trace_elements_metadatafile", "cave_map", "entity_scan",
    "contact", "data_DOI_URL",
]

# Workbook-only columns (QC/admin — not in DB, intentionally excluded)
ENTITY_WORKBOOK_ONLY = {
    "one_and_only", "entity_status_info", "entity_status_notes", "contact_orcid",
}
