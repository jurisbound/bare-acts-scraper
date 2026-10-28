-- Bare Acts tables in schema jb_kb (Azure Postgres), taken from the JurisBound court-data setup
-- (01_kb_setup.sql). Needs the court-data setup first: doc_number_counters (JurisBound numbers)
-- and the extensions.vector type (search index). For reference; the live database already has them.
SET search_path = jb_kb, extensions, public;

-- ---------------------------------------------------------------------------
-- Bare Acts: the five base tables (from jurisbound-web/database/jb/01_jb_schema.sql, section 10)
-- ---------------------------------------------------------------------------
CREATE TABLE bare_acts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code          text UNIQUE NOT NULL,          -- IPC, BNS, CPA2019 …
  title         text NOT NULL,
  short_title   text,
  act_number    int,
  act_year      int NOT NULL,
  jurisdiction  text NOT NULL DEFAULT 'central',  -- central or state code
  category      text,
  status        text NOT NULL DEFAULT 'in_force' CHECK (status IN ('in_force','repealed','partly_repealed')),
  replaced_by_code text,
  source_url    text,
  public_laws_id uuid                           -- link to public.laws citation statistics
);

CREATE TABLE bare_act_aliases (
  id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  act_id   uuid NOT NULL REFERENCES bare_acts(id) ON DELETE CASCADE,
  alias    text NOT NULL,
  UNIQUE (act_id, alias)
);
CREATE INDEX ix_bare_act_aliases_lower ON bare_act_aliases(lower(alias));

CREATE TABLE bare_act_versions (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  act_id             uuid NOT NULL REFERENCES bare_acts(id) ON DELETE CASCADE,
  consolidated_as_of date NOT NULL,
  -- [Azure] pdf_file_id left out: it pointed at the web app's files table; the PDF path is in pdf_path
  status             text NOT NULL DEFAULT 'published' CHECK (status IN ('draft','certified','published','superseded'))
  -- [Azure] certified_by left out: it pointed at the web app's users table (not used yet)
);

CREATE TABLE bare_act_sections (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id   uuid NOT NULL REFERENCES bare_act_versions(id) ON DELETE CASCADE,
  section_no   text NOT NULL,                  -- "302", "103", "138"
  heading      text NOT NULL,
  body         text,                           -- null until ingested from India Code
  chapter      text,
  sort         int NOT NULL,
  tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(heading,'') || ' ' || coalesce(body,''))) STORED,
  UNIQUE (version_id, section_no)
);
CREATE INDEX ix_bare_act_sections_tsv ON bare_act_sections USING gin(tsv);

CREATE TABLE section_mappings (              -- IPC ↔ BNS, CrPC ↔ BNSS, Evidence Act ↔ BSA
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  old_act_code    text NOT NULL,
  old_section     text NOT NULL,
  new_act_code    text NOT NULL,
  new_section     text NOT NULL,
  note            text
);

-- ---------------------------------------------------------------------------
-- Bare Acts: state short codes for Act numbers (JB-ACT-TS-…); on Supabase this table was added by hand
-- ---------------------------------------------------------------------------
CREATE TABLE bare_act_state_codes (
  state_name text PRIMARY KEY,          -- as India Code writes it
  code       text NOT NULL UNIQUE
);
INSERT INTO bare_act_state_codes (state_name, code) VALUES
('Andaman and Nicobar Islands', 'AN'),
  ('Andhra Pradesh', 'AP'),
  ('Arunachal Pradesh', 'AR'),
  ('Assam', 'AS'),
  ('Bihar', 'BR'),
  ('Chandigarh', 'CH'),
  ('Chhattisgarh', 'CG'),
  ('Dadra and Nagar Haveli and Daman and Diu', 'DD'),
  ('Delhi', 'DL'),
  ('Goa', 'GA'),
  ('Gujarat', 'GJ'),
  ('Haryana', 'HR'),
  ('Himachal Pradesh', 'HP'),
  ('Jammu and Kashmir', 'JK'),
  ('Jharkhand', 'JH'),
  ('Karnataka', 'KA'),
  ('Kerala', 'KL'),
  ('Ladakh', 'LA'),
  ('Lakshadweep', 'LD'),
  ('Madhya Pradesh', 'MP'),
  ('Maharashtra', 'MH'),
  ('Manipur', 'MN'),
  ('Meghalaya', 'ML'),
  ('Mizoram', 'MZ'),
  ('Nagaland', 'NL'),
  ('Odisha', 'OD'),
  ('Puducherry', 'PY'),
  ('Punjab', 'PB'),
  ('Rajasthan', 'RJ'),
  ('Sikkim', 'SK'),
  ('Tamil Nadu', 'TN'),
  ('Telangana', 'TS'),
  ('Tripura', 'TR'),
  ('Uttar Pradesh', 'UP'),
  ('Uttarakhand', 'UK'),
  ('West Bengal', 'WB');

-- ---------------------------------------------------------------------------
-- from jurisbound-web/database/jb/04_jb_bare_acts.sql
-- ---------------------------------------------------------------------------
-- =============================================================================
-- JurisBound — bare acts loaded from India Code (schema "jb")
--
-- Adds to the five bare-act tables created by 01_jb_schema.sql:
--   * columns for what India Code gives per Act, per copy and per section
--   * bare_act_parts     schedules, appendices, forms, Statement of Objects
--                        (their text comes from the Act's PDF, not from records)
--   * bare_act_chunks    search index for sections and parts (768 numbers,
--                        the same embedding model as the judgment chunks)
--   * bare_act_load_log  one row per Act per load run (counts, mismatches, errors)
--
-- Safe to run more than once. Deletes nothing. Run after 01_jb_schema.sql.
-- Needs the vector extension (created by the court repos' 02_court_search.sql).
-- =============================================================================

-- [Azure] search_path is set at the top of this file

-- -----------------------------------------------------------------------------
-- 1. One row per Act
-- -----------------------------------------------------------------------------
ALTER TABLE bare_acts
  ADD COLUMN IF NOT EXISTS source           text,          -- 'india_code', 'manual_pdf'
  ADD COLUMN IF NOT EXISTS source_act_id    text,          -- India Code act id, e.g. AC_CEN_5_23_00048_2023-45_…
  ADD COLUMN IF NOT EXISTS source_item_id   text,          -- India Code record id of the Act
  ADD COLUMN IF NOT EXISTS state_name       text,          -- null for central Acts; state name for state Acts
  ADD COLUMN IF NOT EXISTS ministry         text,
  ADD COLUMN IF NOT EXISTS department       text,
  ADD COLUMN IF NOT EXISTS long_title       text,
  ADD COLUMN IF NOT EXISTS preamble         text,
  ADD COLUMN IF NOT EXISTS enacted_on       date,
  ADD COLUMN IF NOT EXISTS enforcement_note text,          -- as published; often a sentence, not one date
  ADD COLUMN IF NOT EXISTS subject_type     text,          -- India Code's own grouping
  ADD COLUMN IF NOT EXISTS summary          text,          -- India Code's summary of the Act
  ADD COLUMN IF NOT EXISTS created_at       timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN IF NOT EXISTS updated_at       timestamptz NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS ux_bare_acts_source_act_id
  ON bare_acts (source_act_id) WHERE source_act_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_bare_acts_jurisdiction_year ON bare_acts (jurisdiction, act_year);

-- -----------------------------------------------------------------------------
-- 2. One row per dated copy of an Act ("as on" date)
-- -----------------------------------------------------------------------------
ALTER TABLE bare_act_versions
  ADD COLUMN IF NOT EXISTS pdf_path        text,           -- storage key: acts/central/2023/act-45-…/2025-10-06/english.pdf
  ADD COLUMN IF NOT EXISTS pdf_sha256      text,           -- fingerprint; a new value means India Code changed the PDF
  ADD COLUMN IF NOT EXISTS pdf_pages       int,
  ADD COLUMN IF NOT EXISTS hindi_pdf_path  text,
  ADD COLUMN IF NOT EXISTS source_pdf_url  text,
  ADD COLUMN IF NOT EXISTS full_text       text,           -- whole PDF text, page by page
  ADD COLUMN IF NOT EXISTS section_count   int,
  ADD COLUMN IF NOT EXISTS fetched_at      timestamptz,
  ADD COLUMN IF NOT EXISTS as_on_source    text,           -- 'pdf' = date printed in the PDF; 'fetch_date' = older PDFs print none
  ADD COLUMN IF NOT EXISTS sections_source text,           -- 'india_code' = section records given by India Code; 'pdf' = sections cut from the PDF text (check these more)
  -- pipeline marks: the scraper sets text_status 'pending'; `acts_cli.py text` sets it 'done'
  -- (or 'needs_sections' / 'failed') and index_status 'pending'; `acts_cli.py index` sets that 'done'
  ADD COLUMN IF NOT EXISTS text_status     text,
  ADD COLUMN IF NOT EXISTS text_note       text,
  ADD COLUMN IF NOT EXISTS index_status    text;

CREATE UNIQUE INDEX IF NOT EXISTS ux_bare_act_versions_act_date
  ON bare_act_versions (act_id, consolidated_as_of);

-- -----------------------------------------------------------------------------
-- 3. One row per section (or per Rule of an Order, as in the Code of Civil Procedure)
-- -----------------------------------------------------------------------------
ALTER TABLE bare_act_sections
  ADD COLUMN IF NOT EXISTS kind              text NOT NULL DEFAULT 'section'
                                             CHECK (kind IN ('section','rule','article')),
  ADD COLUMN IF NOT EXISTS parent            text NOT NULL DEFAULT '',  -- 'ORDER XXXIX: TEMPORARY INJUNCTIONS…' for a rule
  ADD COLUMN IF NOT EXISTS source_section_id text,                      -- India Code section id
  ADD COLUMN IF NOT EXISTS body_html         text,                      -- text as published (line breaks, italics)
  ADD COLUMN IF NOT EXISTS footnotes         text,                      -- amendment history: "Subs. by … (w.e.f. …)"
  ADD COLUMN IF NOT EXISTS pdf_page          int,                       -- page of the PDF where the section starts
  ADD COLUMN IF NOT EXISTS repealed          boolean NOT NULL DEFAULT false;  -- the section itself is repealed or omitted

-- A Rule number repeats under every Order, so the old rule
-- "one section number per copy" becomes "one number per kind and parent".
ALTER TABLE bare_act_sections
  DROP CONSTRAINT IF EXISTS bare_act_sections_version_id_section_no_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_bare_act_sections_number
  ON bare_act_sections (version_id, kind, parent, section_no);
CREATE INDEX IF NOT EXISTS ix_bare_act_sections_version_sort ON bare_act_sections (version_id, sort);

-- -----------------------------------------------------------------------------
-- 4. Parts whose text exists only in the PDF
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bare_act_parts (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version_id  uuid NOT NULL REFERENCES bare_act_versions(id) ON DELETE CASCADE,
  kind        text NOT NULL CHECK (kind IN ('schedule','appendix','annexure','form','statement_of_objects','contents','other')),
  title       text NOT NULL,                       -- "THE FIRST SCHEDULE"
  sort        int  NOT NULL,
  page_from   int,
  page_to     int,
  body        text,                                -- plain text from the PDF pages
  layout      jsonb,                               -- reading-view blocks (tables), same shape as judgments
  source_id   text,                                -- India Code record id, when a title record exists
  tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', coalesce(title,'') || ' ' || coalesce(body,''))) STORED,
  UNIQUE (version_id, kind, sort)
);
CREATE INDEX IF NOT EXISTS ix_bare_act_parts_tsv ON bare_act_parts USING gin (tsv);
-- [Azure] no row-level security

-- -----------------------------------------------------------------------------
-- 5. Search index (meaning search)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bare_act_chunks (
  id           bigserial PRIMARY KEY,
  version_id   uuid NOT NULL REFERENCES bare_act_versions(id) ON DELETE CASCADE,
  section_id   uuid REFERENCES bare_act_sections(id) ON DELETE CASCADE,
  part_id      uuid REFERENCES bare_act_parts(id)    ON DELETE CASCADE,
  chunk_index  int  NOT NULL,
  chunk_text   text NOT NULL,
  embedding    extensions.vector(768),
  CHECK ((section_id IS NOT NULL) <> (part_id IS NOT NULL))   -- exactly one of the two
);
CREATE INDEX IF NOT EXISTS ix_bare_act_chunks_section ON bare_act_chunks (section_id);
CREATE INDEX IF NOT EXISTS ix_bare_act_chunks_part    ON bare_act_chunks (part_id);
CREATE INDEX IF NOT EXISTS ix_bare_act_chunks_version ON bare_act_chunks (version_id);
CREATE INDEX IF NOT EXISTS bare_act_chunks_embedding_hnsw
  ON bare_act_chunks USING hnsw (embedding extensions.vector_cosine_ops);
-- [Azure] no row-level security

-- -----------------------------------------------------------------------------
-- 6. Load log (also the source for the back office)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bare_act_load_log (
  id                 bigserial PRIMARY KEY,
  run_id             uuid NOT NULL,                -- one id per loader run
  source_act_id      text,
  act_title          text,
  act_id             uuid REFERENCES bare_acts(id) ON DELETE SET NULL,
  status             text NOT NULL CHECK (status IN ('loaded','unchanged','mismatch','failed')),
  sections_in_source int,                          -- section records India Code returned
  sections_in_pdf    int,                          -- section headings found in the PDF text
  sections_saved     int,
  parts_saved        int,
  pdf_saved          boolean,
  note               text,                         -- what did not match, or the error
  started_at         timestamptz NOT NULL DEFAULT now(),
  finished_at        timestamptz
);
CREATE INDEX IF NOT EXISTS ix_bare_act_load_log_run ON bare_act_load_log (run_id);
CREATE INDEX IF NOT EXISTS ix_bare_act_load_log_act ON bare_act_load_log (source_act_id, started_at DESC);
-- [Azure] no row-level security

-- -----------------------------------------------------------------------------
-- 7. JurisBound number for an Act
--      central  JB-ACT-CEN-2023-0000001
--      state    JB-ACT-TS-2017-0000001
--    Same method as judgments: the database gives the number when the row is
--    inserted (counter per series and year, in public.doc_number_counters).
--    The year is the Act's year. The number never changes afterwards.
--    State short codes: jb_kb.bare_act_state_codes (section 8), key = state
--    name as India Code writes it. A state with no code yet falls back to the
--    first letters of its name, so an insert never fails for want of a code.
-- -----------------------------------------------------------------------------
ALTER TABLE bare_acts ADD COLUMN IF NOT EXISTS doc_number varchar(40);
CREATE UNIQUE INDEX IF NOT EXISTS uq_bare_acts_doc_number ON bare_acts (doc_number);

CREATE OR REPLACE FUNCTION jb_kb.act_series(p_state_name text) RETURNS text
LANGUAGE sql STABLE
SET search_path = jb_kb  -- [Azure] was public
AS $$
    SELECT 'JB-ACT-' || CASE
        WHEN p_state_name IS NULL OR upper(p_state_name) = 'CENTRAL' THEN 'CEN'
        ELSE COALESCE(
            (SELECT code FROM jb_kb.bare_act_state_codes WHERE state_name = p_state_name),
            upper(left(regexp_replace(p_state_name, '[^A-Za-z]', '', 'g'), 6)))
    END
$$;

CREATE OR REPLACE FUNCTION jb_kb.next_act_number(p_state_name text, p_year integer) RETURNS text
LANGUAGE plpgsql
SET search_path = jb_kb  -- [Azure] was public
AS $$
DECLARE
    v_series text := jb_kb.act_series(p_state_name);
    v_next   integer;
BEGIN
    INSERT INTO doc_number_counters AS c (series, year, last_value)
    VALUES (v_series, p_year, 1)
    ON CONFLICT (series, year) DO UPDATE SET last_value = c.last_value + 1
    RETURNING last_value INTO v_next;
    RETURN v_series || '-' || p_year || '-' || lpad(v_next::text, 7, '0');
END
$$;

CREATE OR REPLACE FUNCTION jb_kb.assign_act_number() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.doc_number IS NULL THEN
        NEW.doc_number := jb_kb.next_act_number(NEW.state_name, NEW.act_year);
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS trg_assign_act_number ON bare_acts;
CREATE TRIGGER trg_assign_act_number BEFORE INSERT ON bare_acts
    FOR EACH ROW EXECUTE FUNCTION jb_kb.assign_act_number();

-- [Azure] no backfill of older Acts: the database starts empty

-- -----------------------------------------------------------------------------
-- 9. For the monthly run
--    source_checksum       fingerprint of the PDF as India Code stores it; the same value
--                          next month means "not changed" without downloading the file
--    source_missing_since  first day a run over the whole list no longer found the Act
--                          on India Code (usually repealed); null while it is listed
-- -----------------------------------------------------------------------------
ALTER TABLE jb_kb.bare_act_versions ADD COLUMN IF NOT EXISTS source_checksum text;
ALTER TABLE jb_kb.bare_acts         ADD COLUMN IF NOT EXISTS source_missing_since date;

-- ---------------------------------------------------------------------------
-- Old law -> new law section links shown in the Act reader (IPC 302 -> BNS 103 …)
-- ---------------------------------------------------------------------------
INSERT INTO section_mappings (old_act_code, old_section, new_act_code, new_section, note) VALUES
('CRPC', '125', 'BNSS', '144', 'Maintenance'),
  ('CRPC', '438', 'BNSS', '482', 'Anticipatory bail'),
  ('IEA', '65B', 'BSA', '63', 'Electronic records'),
  ('IPC', '302', 'BNS', '103', 'Murder'),
  ('IPC', '406', 'BNS', '316', 'Criminal breach of trust — punishment in s.316(2)'),
  ('IPC', '420', 'BNS', '318', 'Cheating — s.318(4)'),
  ('IPC', '498A', 'BNS', '85', 'Cruelty by husband or relatives');

RESET ROLE;
COMMIT;
