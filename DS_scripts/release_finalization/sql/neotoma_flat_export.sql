-- SISAL -> Neotoma (DataBUS) flat export: the full wide-format record of ONE entity.
-- Used by DS_scripts/release_finalization/export_neotoma_transfer.py, which binds the
-- entity_id (?). To run it by hand, replace ? with an entity_id:
--   sqlite3 -csv -header output/sisalv3.1.db < neotoma_flat_export.sql > sisal_entity_903.csv
--
-- Based on Laura Endres' canonical SISAL flat-export query (2026-07-08). Changes:
--   * the entity_id is a parameter instead of a hard-coded value;
--   * 2026-10-09: v3.1 has no free-text entity.contact any more (now
--     entity_link_person -> person). It is rebuilt per entity as `contact` (names)
--     plus `contact_orcid` (ORCIDs, same order), both ", "-separated like v3.0's
--     contact, aggregated in a subquery so entities with several contacts don't
--     multiply rows. Ordered by person_id (v3.1 doesn't keep v3.0's name order).
-- Needs SQLite >= 3.44 (ORDER BY inside group_concat). MySQL equivalent:
--   GROUP_CONCAT(p.name ORDER BY p.person_id SEPARATOR ', ')
--
-- Each table.* keeps its own key columns, so the raw result repeats names such as
-- sample_id, site_id and the whole entity row (self-join compe); the script merges
-- same-named columns where their values agree on every row.

SELECT DISTINCT
    site.*,
    notes.*,
    entity.*,
    contacts.contact,
    contacts.contact_orcid,
    entity_link_reference.*,
    reference.*,
    depths.*,
    dating.*,
    dating_lamina.*,
    sample.*,
    hiatus.*,
    ba_ca.*,
    mg_ca.*,
    p_ca.*,
    sr_ca.*,
    u_ca.*,
    sr_isotopes.*,
    d18o.*,
    d13c.*,
    original_chronology.*,
    sisal_chronology.*,
    compe.*,
    composite_link_entity.*,
    single_ent.*
FROM
    site
LEFT JOIN notes USING (site_id)
LEFT JOIN entity USING (site_id)
LEFT JOIN (
    SELECT elp.entity_id,
           group_concat(p.name, ', ' ORDER BY p.person_id)                AS contact,
           group_concat(coalesce(p.orcid, ''), ', ' ORDER BY p.person_id) AS contact_orcid
    FROM entity_link_person elp
    JOIN person p USING (person_id)
    GROUP BY elp.entity_id
) AS contacts ON contacts.entity_id = entity.entity_id
LEFT JOIN entity_link_reference ON entity.entity_id = entity_link_reference.entity_id
LEFT JOIN reference USING (ref_id)
LEFT JOIN (
    SELECT depth_dating AS depth, entity_id FROM dating
    UNION
    SELECT depth_lam    AS depth, entity_id FROM dating_lamina
    UNION
    SELECT depth_sample AS depth, entity_id FROM sample
) AS depths ON depths.entity_id = entity.entity_id
LEFT JOIN dating        ON dating.entity_id        = entity.entity_id AND dating.depth_dating        = depths.depth
LEFT JOIN dating_lamina ON dating_lamina.entity_id = entity.entity_id AND dating_lamina.depth_lam    = depths.depth
LEFT JOIN sample        ON entity.entity_id         = sample.entity_id AND sample.depth_sample       = depths.depth
LEFT JOIN hiatus        USING (sample_id)
LEFT JOIN ba_ca         USING (sample_id)
LEFT JOIN mg_ca         USING (sample_id)
LEFT JOIN p_ca          USING (sample_id)
LEFT JOIN sr_ca         USING (sample_id)
LEFT JOIN u_ca          USING (sample_id)
LEFT JOIN sr_isotopes   USING (sample_id)
LEFT JOIN d18o          USING (sample_id)
LEFT JOIN d13c          USING (sample_id)
LEFT JOIN original_chronology USING (sample_id)
LEFT JOIN sisal_chronology    USING (sample_id)
LEFT JOIN entity compe                ON entity.entity_id              = compe.entity_id
LEFT JOIN composite_link_entity       ON compe.entity_id               = composite_entity_id
LEFT JOIN entity single_ent           ON composite_link_entity.single_entity_id = single_ent.entity_id
WHERE entity.entity_id = ?
ORDER BY entity.entity_id, depths.depth;
