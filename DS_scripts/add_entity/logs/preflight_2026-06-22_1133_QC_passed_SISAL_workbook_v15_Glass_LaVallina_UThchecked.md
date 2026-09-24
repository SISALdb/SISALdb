# SISAL DB Update — Pre-flight Log

| | |
|---|---|
| **Workbook** | `QC_passed_SISAL_workbook_v15_Glass_LaVallina_UThchecked.xlsx` |
| **DB** | `sisalv4_dev.db` |
| **DB path** | `/Users/lendres/Documents/ResearchHome/00_Researchtopics/Working Groups/AB_SISAL/SISAL-Neo cont./SQL_and_AgeModel/sisalv4_update_dev/sisalv4_dev.db` |
| **Time** | 2026-06-22 11:33 |
| **Errors** | 1 |
| **Warnings** | 1 |
| **Outcome** | ❌ Errors found — not committed |

## Site

| Field | Workbook value | DB action |
|---|---|---|
| site_name | La Vallina | REUSE existing site_id=324 |
| latitude | 43.41 | — |
| longitude | -4.8067 | — |
| elevation | 70 | — |
| country |  | — |
| rock_type |  | — |
| monitoring | yes | — |

## Entities

- **To insert:** 0
- **Skipped (duplicate/error):** 1

| entity_id | persist_id | action | entity_status | entity_name | speleothem_type | d13C | d18O | mineralogy_petrology_fabric | Sr_Ca | Mg_Ca | Ba_Ca | contact | data_DOI_URL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 903 | 324-GLAS | SKIP | current | Glas | stalagmite | yes | yes | yes | yes | yes | yes | Laura Endres |  |

### Entity warnings / errors
- ⚠️ entity row 1: Entity 'Glas' already exists at site_id=324 (entity_id=903, status=current)
- ❌ entity row 1: → Cannot insert duplicate. Check 'one_and_only' and 'entity_status_info' in workbook. Skipping this entity.

## Commit record

<!-- COMMIT_STAMP -->
_Not yet committed._
