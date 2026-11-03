-- =============================================================================
-- JurisBound number of an Act = the Act's official number (JurisBound decision, 3 Nov 2026)
--
--   Hindu Marriage Act, 1955 (Act 25 of 1955)        JB-ACT-CEN-1955-0025
--   Telangana Act 12 of 2019                          JB-ACT-TS-2019-0012
--   no usable official number                         JB-ACT-OD-2010-U001
--
-- Before: a counter per state and year, in the order the Acts were downloaded
-- (most Acts 0000001-0000003, unrelated to the Act's own number).
--
-- The official number is India Code's act_number when it looks real (1-999 and not the
-- year itself: India Code puts notification numbers such as 57063, or the year, in that
-- field for some entries). Otherwise the number the title states, "(18 of 2019)" or
-- "(Delhi Act 11 of 2007)", when its year is the Act's year. Otherwise, and when the
-- number is already taken, "U" and a counter per state and year (series JB-ACT-XX-U).
-- One Act, one number: other India Code entries of the same Act are copies of that Act
-- (bare_act_versions), not Acts of their own.
--
-- Run once on a database set up with 01_bare_acts_tables.sql (which now creates the same
-- functions). Section 2 renumbers the Acts already held, in one transaction: Acts with a
-- real official number first, so a "U" or title number never takes one of theirs.
-- =============================================================================

-- 1. the number
CREATE OR REPLACE FUNCTION jb_kb.act_doc_number(p_id uuid, p_state_name text, p_year integer,
                                                p_number integer, p_title text) RETURNS text
LANGUAGE plpgsql
SET search_path = jb_kb
AS $$
DECLARE
    v_series text := jb_kb.act_series(p_state_name);
    v_no     integer;
    v_m      text[];
    v_cand   text;
    v_next   integer;
BEGIN
    IF p_number BETWEEN 1 AND 999 AND p_number IS DISTINCT FROM p_year THEN
        v_no := p_number;
    ELSE
        -- "(18 of 2019)", "(No.40 of 1963)", "(Delhi Act 11 of 2007)"
        v_m := regexp_match(coalesce(p_title, ''), '\(\s*(?:[A-Za-z.]+\s+)*?(?:No\.?\s*)?(\d{1,3})\s+of\s+(\d{4})\s*\)', 'i');
        IF v_m IS NOT NULL AND v_m[2]::int = p_year AND v_m[1]::int > 0 THEN
            v_no := v_m[1]::int;
        END IF;
    END IF;
    IF v_no IS NOT NULL THEN
        v_cand := v_series || '-' || p_year || '-' || lpad(v_no::text, 4, '0');
        IF NOT EXISTS (SELECT 1 FROM bare_acts WHERE doc_number = v_cand AND id IS DISTINCT FROM p_id) THEN
            RETURN v_cand;
        END IF;
    END IF;
    INSERT INTO doc_number_counters AS c (series, year, last_value)
    VALUES (v_series || '-U', p_year, 1)
    ON CONFLICT (series, year) DO UPDATE SET last_value = c.last_value + 1
    RETURNING last_value INTO v_next;
    RETURN v_series || '-' || p_year || '-U' || lpad(v_next::text, 3, '0');
END
$$;

CREATE OR REPLACE FUNCTION jb_kb.assign_act_number() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.doc_number IS NULL THEN
        NEW.doc_number := jb_kb.act_doc_number(NEW.id, NEW.state_name, NEW.act_year, NEW.act_number, NEW.title);
    END IF;
    RETURN NEW;
END
$$;

-- 2. renumber the Acts already held
DO $$
DECLARE
    r record;
BEGIN
    UPDATE jb_kb.bare_acts SET doc_number = 'OLD-' || id;             -- free every number first
    DELETE FROM jb_kb.doc_number_counters WHERE series LIKE 'JB-ACT-%';   -- the old per-year counters
    FOR r IN SELECT id, state_name, act_year, act_number, title FROM jb_kb.bare_acts
              ORDER BY coalesce(act_number BETWEEN 1 AND 999 AND act_number IS DISTINCT FROM act_year, false) DESC,
                       created_at, id LOOP
        UPDATE jb_kb.bare_acts
           SET doc_number = jb_kb.act_doc_number(r.id, r.state_name, r.act_year, r.act_number, r.title)
         WHERE id = r.id;
    END LOOP;
END
$$;
