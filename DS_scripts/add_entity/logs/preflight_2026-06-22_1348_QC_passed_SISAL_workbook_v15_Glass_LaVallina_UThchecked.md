# SISAL DB Update — Pre-flight Log

| | |
|---|---|
| **Workbook** | `QC_passed_SISAL_workbook_v15_Glass_LaVallina_UThchecked.xlsx` |
| **DB** | `sisalv4_dev.db` |
| **DB path** | `/Users/lendres/Documents/ResearchHome/00_Researchtopics/Working Groups/AB_SISAL/SISAL-Neo cont./SQL_and_AgeModel/sisalv4_update_dev/sisalv4_dev.db` |
| **Time** | 2026-06-22 13:48 |
| **Errors** | 0 |
| **Warnings** | 0 |
| **Outcome** | ✅ Committed |

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

- **To insert:** 1
- **Skipped (duplicate/error):** 0

| entity_id | persist_id | action | entity_status | entity_name | speleothem_type | d13C | d18O | mineralogy_petrology_fabric | Sr_Ca | Mg_Ca | Ba_Ca | contact | data_DOI_URL |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 903 | 324-GLAS | INSERT | current | Glas | stalagmite | yes | yes | yes | yes | yes | yes | Laura Endres | NA |

## Planned database updates

Data from workbook `QC_passed_SISAL_workbook_v15_Glass_LaVallina_UThchecked.xlsx` will update the following tables in `sisalv4_dev.db`:

| Table | Action | Rows |
|---|---|---|
| `site` | REUSE existing site_id=324 (no INSERT) | — |
| `entity` | INSERT | 1 |
| `dating` | INSERT (dating_id 15726–15750) | 25 |
| `sample` | INSERT (sample_id 507791–508322) | 532 |
| `original_chronology` | INSERT | 532 |
| `d18O` | INSERT | 532 |
| `d13C` | INSERT | 532 |
| `Sr_Ca` | INSERT | 532 |
| `Mg_Ca` | INSERT | 532 |
| `reference` | INSERT 1 new, REUSE 0 | 1 |
| `entity_link_reference` | INSERT | 1 |
| `notes` | INSERT | 2 |

## Commit record

<!-- COMMIT_STAMP -->
**Committed:** 2026-06-22 13:48
