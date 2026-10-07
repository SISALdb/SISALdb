# SISAL DB Update -- Pre-flight Report

| | |
|---|---|
| **Workbook** | `v2s1_2026-10-07_steward-corrected.xlsx` |
| **Date** | 2026-10-07 10:05 |
| **Site action** | INSERT (site_id=368) |
| **Entities to insert** | 2 |
| **Errors** | 0 |
| **Warnings** | 0 |
| **Committed at** | 2026-10-07 10:05 |
| **Status** | COMMITTED |

## Entities

- `TC-2` -> insert (entity_id=904, persist_id=368-TC2)
- `TC-7` -> insert (entity_id=905, persist_id=368-TC7)

## Steward note on the Auto-QC gate (Susie, 2026-10-07)
Workbook v2s1 (md5 3a0d97e10ef3329aa1d30be47523753f) = steward-corrected copy of Bryce Belanger's v2 (2026-10-06). **1 known false-positive QC error** remains: "At entity TC-7, recalculated corrected ages deviate 8.9 % …" (WebSubmit's corrected-age check ignores `chem_year`; reported to Gergő). Accepted as a false positive by Laura on 2026-10-06. All other QC errors were resolved in v2s1 (hiatus rows blanked, matching 'Event; hiatus' dating rows added). The gate prompt was answered "y" on that basis.
