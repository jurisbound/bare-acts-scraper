-- =============================================================================
-- The whole Act as printed (JurisBound decision, 4 Nov 2026)
--
-- The text step (court-pipeline `acts_cli.py text`) saves, for every Act copy, the
-- reading layout of the whole PDF: headings, centred and right-aligned lines,
-- paragraphs, the side notes printed in the margin ("Short title.") and page
-- breaks. The website shows it for an Act whose sections could not be cut, in
-- place of the plain full text. Before, only the schedules and forms had a layout
-- (bare_act_parts.layout).
--
--   {"v": 1, "blocks": [{"k": "heading", "t": "THE … ACT, 1987"},
--                       {"k": "side", "t": "Short title."},
--                       {"k": "para", "t": "1. This Act may be called …"}, …]}
--
-- Run once on a database set up before this date (01_bare_acts_tables.sql now
-- creates the column too). Adds a column; nothing else changes.
-- =============================================================================
ALTER TABLE jb_kb.bare_act_versions
  ADD COLUMN IF NOT EXISTS layout jsonb;
