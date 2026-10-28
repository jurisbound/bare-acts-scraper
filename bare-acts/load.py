"""Bare acts scraper: collect Acts from India Code into the JurisBound database.

    python load.py --title "The Bharatiya Nyaya Sanhita, 2023"      one central Act
    python load.py --titles-file sample_acts.txt                    a list of central Acts
    python load.py --all-central --limit 20                         central Acts, first 20
    python load.py --state "Telangana" --limit 5                    a state's own Acts, first 5
    python load.py --repealed "The Indian Penal Code, 45 of 1860"   an Act from the Centre's repealed list
    python load.py ... --dry-run                                    read and count only; saves nothing
    python load.py ... --force                                      collect again even if the PDF has not changed

This is the light half of the work, like the court scrapers. For each Act it saves
  1. the Act's details                          -> bare_acts
  2. the English PDF                            -> storage, acts/...
  3. the section and rule records, when India Code gives them
                                                -> bare_act_sections
and marks the copy "text pending". It does not open the PDF.

Everything that needs the PDF's text (chapters, schedules, tables, the "as on"
date, and cutting sections when India Code gives none) is done afterwards by
the pipeline: `python acts_cli.py text`, then `python acts_cli.py index`.

An Act whose PDF is the same file as the saved copy is skipped ("unchanged"),
so the same command can run every month and only new or amended Acts do work.
One Act is one database transaction: an Act that fails leaves nothing half-saved.
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from datetime import date, datetime
from typing import Optional

import india_code as ic
import jb_db

MAX_PDF_BYTES = 60 * 1024 * 1024


def _int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _date(v) -> Optional[date]:
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def pick_english_pdf(files: list[dict]) -> Optional[dict]:
    """India Code names the English text a<year>-<number>.pdf and the Hindi one h….pdf
    or <year><number>.pdf. With no such name the smallest PDF is taken; the pipeline
    checks the language when it reads the file."""
    pdfs = [f for f in files if f["name"].lower().endswith(".pdf") and f["size"] <= MAX_PDF_BYTES]
    named = [f for f in pdfs if re.match(r"^a\d{4}", f["name"], re.I)]
    if named:
        return min(named, key=lambda f: f["size"])
    return min(pdfs, key=lambda f: f["size"]) if pdfs else None


def rule_number(title: str) -> tuple[str, str]:
    """'Rule 4A. Procedure where…' -> ('4A', 'Procedure where…')."""
    # the records are typed by hand: "Rule11 A. …", "Rule. 25. …", "Ruule46C. …", "Rule46 I.Application…"
    m = re.match(r"^\s*Ru+le\s*\.?\s*([0-9]+)\s?-?\s?([A-Z]{1,3}(?=\s*\.))?\s*\.?\s*(.*)$", title or "", re.S)
    if not m:
        return "", (title or "").strip()
    return m.group(1) + (m.group(2) or ""), m.group(3).strip()


_REPEALED_HEAD = re.compile(r"^\W*(repealed|omitted)\W*$", re.I)
_REPEALED_BODY = re.compile(r"^\s*\[([^\]]{3,300})\]\W*(Rep\.|Repealed|Omitted)", re.I | re.S)


def repealed_heading(heading: str, body: str) -> Optional[str]:
    """A repealed section's record is headed just 'Repealed.'; its old heading is
    in square brackets at the start of the text. Returns that old heading
    ('' when there is none), or None when the section is not repealed."""
    if not _REPEALED_HEAD.match(heading or ""):
        return None
    m = _REPEALED_BODY.match(body or "")
    return m.group(1).strip().rstrip(".") if m else ""


def section_rows(groups: dict) -> list[dict]:
    """Rows for bare_act_sections from India Code's records. The chapter, the
    PDF page and the Order of a rule are not in the records; the pipeline fills them."""
    rows = []
    for i, s in enumerate(groups.get("SECTION", [])):
        number, heading = ic.field(s, "section_number") or "", s["name"] or ""
        body = ic.html_to_text(ic.field(s, "section_page_note")) or None
        old_heading = repealed_heading(heading, body or "")
        rows.append({
            "kind": "section", "parent": "",
            "section_no": number or f"#{i + 1}",
            "heading": (old_heading or "Repealed") if old_heading is not None else (heading or "(no heading)"),
            "repealed": old_heading is not None,
            "body": body, "body_html": ic.field(s, "section_page_note"),
            "footnotes": "\n".join(ic.html_to_text(f) for f in ic.fields(s, "section_footnote")) or None,
            "chapter": None, "sort": i + 1, "source_section_id": ic.field(s, "section_id"), "pdf_page": None,
        })
    for i, r in enumerate(groups.get("SCHORDRULE", [])):
        rid = ic.field(r, "schordrule_id") or str(i)
        number, heading = rule_number(r["name"])
        rows.append({
            "kind": "rule", "parent": f"(Order not identified) #{rid}", "repealed": False,
            "section_no": number or f"#{rid}", "heading": heading or "(no heading)",
            "body": ic.html_to_text(ic.field(r, "schordrule_description")) or None,
            "body_html": ic.field(r, "schordrule_description"),
            "footnotes": None, "chapter": None, "sort": 10 ** 9 + i, "source_section_id": rid, "pdf_page": None,
        })
    return rows


_REPEALED_TITLE = re.compile(r"^(?P<name>.*?),?\s*(?P<no>\d+)\s+of\s+(?P<year>\d{4})\s*,?\s*\(\s*Rep\.,?\s*(?:Act\s*)?(?P<by_no>\d+)\s+of\s+(?P<by_year>\d{4})", re.I)


def repealed_details(item: dict) -> dict:
    """A repealed Act's record has only a title, such as
    "The Indian Penal Code, 45 of 1860 (Rep., Act 45 of 2023)". Everything known is read from it."""
    m = _REPEALED_TITLE.match(item["name"])
    if not m:
        raise RuntimeError(f"cannot read the Act number and year from the title: {item['name']}")
    name = m.group("name").strip().rstrip(",")
    number, year = int(m.group("no")), int(m.group("year"))
    named_year = re.search(r",?\s*(\d{4})$", name)                 # "The Code of Criminal Procedure 1973"
    if named_year:
        name = re.sub(r",?\s*\d{4}$", "", name) + f", {named_year.group(1)}"
    else:
        name = f"{name}, {year}"
    by = (int(m.group("by_no")), int(m.group("by_year")))
    return {"title": name, "number": number, "year": year, "replaced_by": jb_db.SHORT_CODES.get(by),
            "note": f"Repealed by Act {by[0]} of {by[1]}"}


def load_act(con, run_id: str, act: dict, dry_run: bool, force: bool) -> str:
    repealed = act.get("_repealed")
    old = repealed_details(act) if repealed else None
    title = old["title"] if old else act["name"]
    # a repealed record has no India Code act id; its record id identifies it
    act_id_src = f"REPEAL:{act['uuid']}" if repealed else ic.field(act, "act_id")
    entry = {"source_act_id": act_id_src, "act_title": title, "started_at": datetime.now()}
    try:
        files = ic.act_files(act["uuid"])
        chosen = pick_english_pdf(files)
        if not chosen:
            raise RuntimeError(f"no PDF among the Act's files: {[f['name'] for f in files]}")
        with con.cursor() as cur:
            saved = jb_db.existing_version(cur, act_id_src)
        # Quick check, no download: India Code lists a fingerprint of each stored file.
        # The same fingerprint as last time means the PDF was not replaced.
        if saved and saved.get("checksum") and saved["checksum"] == chosen.get("checksum") and not force:
            if not dry_run:
                with con, con.cursor() as cur:
                    jb_db.log(cur, run_id, {**entry, "status": "unchanged", "note": "same PDF as the saved copy"})
            print(f"  unchanged  {title}")
            return "unchanged"

        # a dry run only counts: it does not download the PDF
        data = b"" if dry_run else ic.download(chosen["url"])
        digest = None if dry_run else jb_db.sha256(data)
        if not dry_run and saved and saved["sha"] == digest and not force:
            # a copy saved before fingerprints were kept: remember it, so next time needs no download
            with con, con.cursor() as cur:
                jb_db.remember_checksum(cur, saved["id"], chosen.get("checksum"))
                jb_db.log(cur, run_id, {**entry, "status": "unchanged", "note": "same PDF as the saved copy"})
            print(f"  unchanged  {title}")
            return "unchanged"
            if not dry_run:
                with con, con.cursor() as cur:
                    jb_db.log(cur, run_id, {**entry, "status": "unchanged", "note": "same PDF as the saved copy"})
            print(f"  unchanged  {title}")
            return "unchanged"

        groups = {} if repealed else ic.act_records(act_id_src)      # the repealed list gives a PDF only
        rows = section_rows(groups)
        n_sections = sum(1 for r in rows if r["kind"] == "section")
        n_rules = len(rows) - n_sections
        notes = []
        if not n_sections:
            notes.append("India Code gives no section records; the pipeline must cut sections from the PDF")
        empty = sum(1 for r in rows if not r["body"])
        if empty:
            notes.append(f"{empty} records have no text")
        number, year = (old["number"], old["year"]) if old else (_int(ic.field(act, "act_number")), _int(ic.field(act, "act_year")))
        if not year:
            raise RuntimeError("the Act record has no year")
        state = ic.field(act, "state_name")
        state = None if state == "CENTRAL" else state
        # same PDF as before (a forced reload) keeps its copy and its place in storage
        same_copy = bool(saved and saved["sha"] == digest)
        fetched = saved["as_on"] if same_copy else date.today()
        key = saved["pdf_path"] if same_copy and saved.get("pdf_path") else jb_db.storage_key(state, year, number, title, fetched)
        print(f"  collected  {title} | sections {n_sections} | rules {n_rules} | "
              f"PDF {chosen['name']} {chosen['size'] / 1e6:.1f} MB" + (f" | note: {'; '.join(notes)}" if notes else ""))
        if dry_run:
            return "loaded"

        if not same_copy:
            jb_db.upload_pdf(key, data)
        with con, con.cursor() as cur:
            act_row = jb_db.save_act(cur, {
                "code": jb_db.act_code(number, year, state, act_id_src), "title": title, "act_number": number, "act_year": year,
                "jurisdiction": "central" if not state else "state",
                "status": "repealed" if repealed or ic.field(act, "repealed") == "true" else "in_force",
                "replaced_by_code": old["replaced_by"] if old else None,
                "source_url": f"https://indiacode.gov.in/handle/{act['handle']}", "source": "india_code",
                "source_act_id": act_id_src, "source_item_id": act["uuid"], "state_name": state,
                "ministry": ic.field(act, "ministry_name"), "department": ic.field(act, "department_name"),
                "long_title": ic.field(act, "title.long_title"),
                "preamble": ic.html_to_text(ic.field(act, "preamble_description")) or None,
                "enacted_on": _date(ic.field(act, "enact_date")),
                "enforcement_note": old["note"] if old else ic.field(act, "enforcement_date"),
                "subject_type": ic.field(act, "type"), "summary": ic.field(act, "description.abstract"),
            })
            version = jb_db.save_version(cur, act_row, {
                "as_on": fetched, "pdf_path": key, "pdf_sha256": digest, "source_pdf_url": chosen["url"],
                "section_count": n_sections, "sections_source": "india_code" if n_sections else None,
                "source_checksum": chosen.get("checksum"),
            })
            saved_rows = jb_db.save_sections(cur, version, rows)
            if saved_rows != len(rows):
                notes.append(f"{len(rows) - saved_rows} records skipped: number repeated")
            jb_db.log(cur, run_id, {**entry, "act_id": act_row, "status": "loaded", "note": "; ".join(notes) or None,
                                    "sections_in_source": n_sections, "sections_saved": saved_rows, "pdf_saved": True})
        return "loaded"
    except Exception as e:
        con.rollback()
        print(f"  FAILED     {title}: {e}")
        if not dry_run:
            with con, con.cursor() as cur:
                jb_db.log(cur, run_id, {**entry, "status": "failed", "note": str(e)[:1000]})
        return "failed"


def main() -> int:
    ap = argparse.ArgumentParser(description="Collect bare acts from India Code.")
    ap.add_argument("--title", action="append", default=[], help="exact title of a central Act (repeatable)")
    ap.add_argument("--titles-file", help="text file, one Act title per line")
    ap.add_argument("--all-central", action="store_true", help="every central Act on India Code")
    ap.add_argument("--state", action="append", default=[],
                    help='every Act of one state or union territory, by its India Code name, e.g. "Telangana" '
                         "(repeatable; use --limit for a few)")
    ap.add_argument("--repealed", action="append", default=[],
                    help='a repealed central Act, by the start of its India Code title, '
                         'e.g. "The Indian Penal Code, 45 of 1860" (repeatable)')
    ap.add_argument("--limit", type=int, help="stop after this many Acts")
    ap.add_argument("--dry-run", action="store_true", help="read and count only; save nothing")
    ap.add_argument("--force", action="store_true", help="collect again even when the PDF has not changed")
    args = ap.parse_args()

    titles = list(args.title)
    if args.titles_file:
        with open(args.titles_file, encoding="utf-8") as f:
            titles += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not titles and not args.all_central and not args.repealed and not args.state:
        ap.error("give --title, --titles-file, --all-central, --state or --repealed")

    acts = []
    whole_lists: list[tuple[str, Optional[str], set[str]]] = []      # lists read in full this run
    if args.all_central:
        acts = list(ic.central_acts())
        if not args.limit:
            whole_lists.append(("central", None, {ic.field(a, "act_id") for a in acts}))
    for t in titles:
        act = ic.find_central_act(t)
        if act:
            acts.append(act)
        else:
            print(f"  NOT FOUND  {t} (no central Act with this exact title on India Code)")
    for st in args.state:
        found = []
        for act in ic.state_acts(st):
            found.append(act)
            if args.limit and len(found) >= args.limit:      # --limit is per state
                break
        acts += found
        if not args.limit:
            whole_lists.append(("state", st, {ic.field(a, "act_id") for a in found}))
    for t in args.repealed:
        item = ic.find_repealed_act(t)
        if item:
            acts.append({**item, "_repealed": True})
        else:
            print(f"  NOT FOUND  {t} (no repealed central Act whose title starts like this)")
    if args.limit and not args.state:
        acts = acts[:args.limit]

    run_id = str(uuid.uuid4())
    con = jb_db.connect()
    counts: dict[str, int] = {}
    print(f"run {run_id} | {len(acts)} Acts | {'DRY RUN, nothing saved' if args.dry_run else 'saving'}")
    for act in acts:
        result = load_act(con, run_id, act, args.dry_run, args.force)
        counts[result] = counts.get(result, 0) + 1
    if not args.dry_run:
        for jurisdiction, st, seen in whole_lists:
            with con, con.cursor() as cur:
                gone = jb_db.mark_missing(cur, jurisdiction, st, seen)
            for t in gone:
                print(f"  NO LONGER ON INDIA CODE  {t}")
            counts["no longer listed"] = counts.get("no longer listed", 0) + len(gone)
    con.close()
    print(f"done: {counts}")
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
