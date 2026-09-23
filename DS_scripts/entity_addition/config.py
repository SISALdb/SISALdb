"""
Paths and constants for the SISAL DB update pipeline (SISALv3.1, CSV-first).
"""
from pathlib import Path

# -- Paths --------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CSV_DIR = REPO_ROOT / "csv"  # the repo's actual source of truth

# -- Workbook sheet -> DB table mapping ----------------------------------------
# Sheets handled so far; extend as more tables are implemented.
SHEET_TO_TABLE = {
    "Site metadata":    "site",
    "Entity metadata":  "entity",
}

# Entity metadata columns present in the workbook that map directly to
# entity.csv columns. Columns in the workbook that do NOT exist in entity.csv
# are silently dropped.
#
# NOT included here (handled separately, see add_entity.py):
#   - "contact" / "contact_orcid": entity.contact no longer exists as of the
#     v3.1 release-management schema -- contacts are now entity_link_person
#     rows (junction to person.csv), resolved via the same person-search-or-
#     create flow as add_project.py.
#   - added_in_release_id / last_modified_release_id: new provenance columns
#     with no workbook equivalent -- left blank on insert (see add_entity.py).
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
    "data_DOI_URL",
]

# Workbook-only columns (QC/admin, or handled separately -- not written
# directly into entity.csv via ENTITY_WORKBOOK_COLS)
ENTITY_WORKBOOK_ONLY = {
    "one_and_only", "entity_status_info", "entity_status_notes",
    "contact", "contact_orcid",
}
