# Project ID ranges

When a project branch adds brand-new rows (new entities, samples, dates, references), it needs
its own reserved block of primary-key IDs — otherwise two branches that both continue
sequentially from wherever `main` happened to be when they forked will independently assign the
same IDs to different records, and merging silently collides. See the discussion in
`release-management framework.md` (Obsidian vault) for the full reasoning.

**Rule:** before a project branch starts adding data, reserve it a block here. Every new row the
project adds — in any of the tables listed below — gets an ID from its block, starting at the low
end and incrementing upward. Never reuse a block once it's reserved, even if the project stalls
or gets abandoned.

**Applies to:** `entity_id`, `sample_id`, `dating_id`, `dating_lamina_id`, `reference.ref_id` —
every table with its own auto-incrementing primary key that a project might insert brand-new rows
into. Use the *same* block number across all of these for one project (e.g. Hydro2K = the 10000s
everywhere) rather than tracking a different range per table.

`person_id` is intentionally not included — new contributors are added rarely enough that a
quick check before inserting is enough; a reserved-block scheme isn't worth the overhead there.

## Reserved blocks

| Project | Block | Status | Reserved | Notes |
|---|---|---|---|---|
| Hydro2K | `10000–19999` | reserved | 2026-08-20 | First project block. Not yet actively adding entities. |

## Next available block

`20000–29999` — reserve this for whichever project needs one next, and add a row above rather
than reusing Hydro2K's range even if it turns out mostly unused.
