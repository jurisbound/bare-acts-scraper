"""Bare acts scraper: collect Acts from India Code into the JurisBound database.

    python load.py --title "The Bharatiya Nyaya Sanhita, 2023"      one central Act
    python load.py --titles-file sample_acts.txt                    a list of central Acts
    python load.py --all-central --limit 20                         central Acts, first 20
    python load.py --state "Telangana" --limit 5                    a state's own Acts, first 5
    python load.py --repealed "The Indian Penal Code, 45 of 1860"   one Act from the Centre's repealed list
    python load.py --all-repealed                                  every Act in the Centre's repealed list
    python load.py --gazette URL --act-number 30 --act-year 2025 --gazette-title "The Income-tax Act, 2025"
                                                                    a new Act from the Gazette of India, before
                                                                    India Code has it (India Code's copy takes
                                                                    over the same Act when it appears)
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
import difflib
import html
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
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


def _not_english(title: str) -> bool:
    """A title in another script (Malayalam, Devanagari): the Act's Malayalam/Hindi entry."""
    letters = [c for c in title or "" if c.isalpha()]
    return bool(letters) and sum(not c.isascii() for c in letters) > len(letters) * 0.5


_TITLE_SKIP = {"the", "act", "acts", "and", "of", "in", "for", "single", "document", "doc", "amendment", "no", "state"}


def _same_title(held: str, new: str, state: str) -> bool:
    """Two India Code entries with one number and year are the same Act when their titles are:
    the same words, the same words spelt differently ("Salaraies", "CO-OPERATIVE"/"COOPERATIVE"),
    or one of them in Malayalam/Hindi (its English entry carries the same number)."""
    if _not_english(held) or _not_english(new):
        return True
    skip = _TITLE_SKIP | set(re.findall(r"[a-z]+", (state or "").lower()))

    def words(t):
        return {re.sub(r"s$", "", w) for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in skip}

    a, b = words(held), words(new)
    if not a or not b:
        return True                                    # too little to tell apart: one Act
    if len(a & b) / len(a | b) >= 0.5:
        return True
    return difflib.SequenceMatcher(None, "".join(sorted(a)), "".join(sorted(b))).ratio() >= 0.8


def _uploaded_on(act_id: str) -> Optional[date]:
    """The day India Code put the entry up: its id ends in the time in milliseconds
    ("AC_AS_58_529_00069_00069_1591090210163" -> 2 June 2020)."""
    m = re.search(r"_(\d{13})$", act_id or "")
    return datetime.utcfromtimestamp(int(m.group(1)) / 1000).date() if m else None


def save_extra_copy(con, run_id: str, act: dict, entry: dict, title: str, chosen: dict, same_act: dict,
                    dry_run: bool) -> str:
    """Another India Code entry of an Act we hold: its PDF (and any section records) is kept as
    another copy of that Act, not shown; the copy shown stays as it is."""
    act_id_src = entry["source_act_id"]
    print(f"  extra copy {title} -> {same_act['code']} | PDF {chosen['name']} {chosen['size'] / 1e6:.1f} MB")
    if dry_run:
        return "extra copy kept"
    data = ic.download(chosen["url"])
    rows = section_rows(ic.act_records(act_id_src))
    n_sections = sum(1 for r in rows if r["kind"] == "section")
    year = _int(ic.field(act, "act_year"))
    with con.cursor() as cur:
        as_on = jb_db.free_date(cur, same_act["id"], _uploaded_on(act_id_src) or date.today())
    # its own file: the shown copy's PDF is never overwritten
    key = jb_db.storage_key(ic.field(act, "state_name"), year, _int(ic.field(act, "act_number")), title, as_on,
                            language="copy-" + jb_db.sha256(act_id_src.encode())[:8])
    jb_db.upload_pdf(key, data)
    with con, con.cursor() as cur:
        version = jb_db.save_version(cur, same_act["id"], {
            "as_on": as_on, "pdf_path": key, "pdf_sha256": jb_db.sha256(data), "source_pdf_url": chosen["url"],
            "section_count": n_sections, "sections_source": "india_code" if n_sections else None,
            "source_checksum": chosen.get("checksum"),
        }, publish=False)
        jb_db.save_sections(cur, version, rows)
        jb_db.log(cur, run_id, {**entry, "act_id": same_act["id"], "status": "loaded", "pdf_saved": True,
                                "note": f"another India Code entry of {same_act['code']} (\"{same_act['title'][:60]}\"), "
                                        "kept as another copy of that Act; the copy shown is unchanged"})
    return "extra copy kept"


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


# A repealed Act's title in India Code carries everything known about it, in many shapes:
#   "The Indian Penal Code, 45 of 1860 (Rep., Act 45 of 2023)"
#   "Police Presidency towns (amending Act 13 of 1856), 1860, 48 of 1860 (Repealed (in Bombay), Bom. Act 4 of 1902 …)"
#   "The Central Provinces Courts Act, 1865, 14 of 1865 (Repealed (as to Oudh), Act 32 of 1871 Repealed (…), Act, 16 of 1885)"
# The Act's own number and year are the LAST "N of YYYY" before the repeal note; the repealing central
# Act is the last "Act N of YYYY" in the note that is not a provincial Act ("Bom. Act 4 of 1902").
_REPEAL_START = re.compile(r"\(\s*Rep(?:ealed)?\b", re.I)
_NO_OF_YEAR = re.compile(r"(\d{1,4})\s+of\s+(\d{4})\b", re.I)
# the Act's own "N of YYYY" as India Code types it, typos included: "49of 1952", "12 o f 1947", "4 to 1900"
_OWN_NO_OF_YEAR = re.compile(r"(\d{1,4})\s*(?:o\s?f|to)\s+(\d{4})\b", re.I)
# no number in the title: India Code's file name usually has it ("A2021-33.pdf", "1878-8.pdf")
_FILE_NO = re.compile(r"^A?(\d{4})-(\d{1,4})\.pdf$", re.I)
_PROVINCIAL = re.compile(r"\b(?:Bom|Ben|Beng|Bengal|Bombay|Mad|Madras|Pun|Punj|Bih|Bihar|Ori|Orissa|Assam|Raj|Guj|Mah|Mys|Ker|Hyd|"
                         r"Sind|Bur|Burma|Oudh|U\.\s?P|C\.\s?P|E\.\s?B|W\.\s?B|A\.\s?P|T\.\s?N|M\.\s?P)\.?\s*,?\s*(?:Act\s*,?\s*)?(?:No\.?\s*)?$", re.I)


def repealed_details(item: dict) -> dict:
    """Name, number, year and the repealing Act, read from a repealed Act's title."""
    raw = re.sub(r"\s+", " ", html.unescape(item["name"])).strip().strip("\"'")
    start = _REPEAL_START.search(raw)
    head, tail = (raw[:start.start()], raw[start.start():]) if start else (raw, "")
    own = list(_OWN_NO_OF_YEAR.finditer(head))
    if own:
        number, year = int(own[-1].group(1)), int(own[-1].group(2))
        name = head[:own[-1].start()].strip(" ,;:")
    else:
        # "The Sea Customs Act, 1878 (Rep., …)": the year only; the number comes from the file name
        # (load_act), and an Act of the British Parliament has none ("Government of India Act, 1935")
        years = list(re.finditer(r"\b(1[6-9]\d\d|20\d\d)\b", head))
        if not years:
            raise RuntimeError(f"cannot read the Act number and year from the title: {item['name']}")
        number, year = None, int(years[-1].group(1))
        name = head.strip(" ,;:")
    if name.startswith("(") and name.count("(") > name.count(")"):       # a stray "(" in India Code's title
        name = name[1:].strip()
    named_year = re.search(r",?\s*(\d{4})$", name)                 # "The Code of Criminal Procedure 1973"
    if named_year:
        name = re.sub(r",?\s*\d{4}$", "", name).strip(" ,") + f", {named_year.group(1)}"
    else:
        name = f"{name}, {year}"
    central = [m for m in _NO_OF_YEAR.finditer(tail) if not _PROVINCIAL.search(tail[max(0, m.start() - 16):m.start()])]
    by = (int(central[-1].group(1)), int(central[-1].group(2))) if central else None
    note = f"Repealed by Act {by[0]} of {by[1]}" if by else "Repealed: " + tail.strip("() ")[:300] if tail else "Repealed"
    return {"title": name, "number": number, "year": year, "by": by, "replaced_by": None, "note": note,
            "says_repealed": bool(start)}


def number_from_file(files: list[dict], year: int) -> Optional[int]:
    """The Act number in India Code's file name ("A2021-33.pdf"), when it is for the same year."""
    for f in files:
        m = _FILE_NO.match(f.get("name") or "")
        if m and int(m.group(1)) == year:
            return int(m.group(2))
    return None


def load_act(con, run_id: str, act: dict, dry_run: bool, force: bool, skip_saved: bool = False) -> str:
    repealed = act.get("_repealed")
    title = act["name"]
    # a repealed record has no India Code act id; its record id identifies it
    act_id_src = f"REPEAL:{act['uuid']}" if repealed else ic.field(act, "act_id")
    entry = {"source_act_id": act_id_src, "act_title": title, "started_at": datetime.now()}
    try:
        old = repealed_details(act) if repealed else None      # a title it cannot read fails this Act only
        files = None
        if old and old["number"] is None:
            files = ic.act_files(act["uuid"])
            old["number"] = number_from_file(files, old["year"])
        if old:
            title = entry["act_title"] = old["title"]
            with con.cursor() as cur:
                # the repealing Act (BNS for IPC, …) when we hold it; otherwise linked later (link_repealing_acts)
                old["replaced_by"] = jb_db.code_for_act(cur, *old["by"]) if old["by"] else None
                held = jb_db.central_act_by_number(cur, old["number"], old["year"]) if old["number"] else None
            if held and held["status"] != "repealed" and not old["says_repealed"]:
                # India Code lists it among repealed Acts but its title has no repeal note
                # ("The Tribunals Reforms Act, 2021", in force): the Act we hold is left as it is
                if not dry_run:
                    with con, con.cursor() as cur:
                        jb_db.log(cur, run_id, {**entry, "act_id": held["id"], "status": "unchanged",
                                                "note": "in India Code's repealed list without a repeal note; left in force"})
                print(f"  unchanged  {title} (in the repealed list without a repeal note; left in force)")
                return "unchanged"
            if held:
                # an Act we hold from the list of Acts in force, now repealed: the same row changes status
                # (keeps its JurisBound number, sections and saved items); no second row, no download
                if held["status"] == "repealed" and (old["replaced_by"] is None or held["replaced_by"] == old["replaced_by"]):
                    if not dry_run:
                        with con, con.cursor() as cur:
                            jb_db.log(cur, run_id, {**entry, "act_id": held["id"], "status": "unchanged",
                                                    "note": "held as repealed"})
                    print(f"  unchanged  {title} (held as repealed)")
                    return "unchanged"
                print(f"  repealed   {title}: the Act we hold is now marked repealed ({old['note']})")
                if not dry_run:
                    with con, con.cursor() as cur:
                        jb_db.mark_repealed(cur, held["id"], old["replaced_by"], old["note"])
                        jb_db.log(cur, run_id, {**entry, "act_id": held["id"], "status": "loaded",
                                                "note": f"now repealed: {old['note']}"})
                return "loaded"
        if skip_saved and not repealed and not force:
            # finishing a big state over several runs: an Act already saved is not checked with India
            # Code again (no file list read, no two-second pause); the weekly change check does that
            with con.cursor() as cur:
                held_copy = jb_db.existing_version(cur, act_id_src)
            if held_copy:
                if not dry_run:
                    with con, con.cursor() as cur:
                        jb_db.log(cur, run_id, {**entry, "status": "unchanged",
                                                "note": "already saved; not checked again (--skip-saved)"})
                print(f"  unchanged  {title} (already saved; not checked again)")
                return "unchanged"
        files = files if files is not None else ic.act_files(act["uuid"])
        if not any(f["name"].lower().endswith(".pdf") for f in files):
            # Not an Act with a missing PDF: India Code files only the state's rules, notifications or
            # orders under this entry (mostly central Acts listed under a state), or just a title.
            # Nothing to collect; logged as such, not as a failure.
            groups = {} if repealed else ic.act_records(act_id_src)
            held_kinds = sorted(k for k, v in groups.items() if v and k != "ACT")
            what = f"only the state's {', '.join(k.lower() + 's' for k in held_kinds)}" if held_kinds else "only a title"
            note = f"no Act PDF on India Code ({what})"
            if not dry_run:
                with con, con.cursor() as cur:
                    jb_db.log(cur, run_id, {**entry, "status": "mismatch", "note": note, "pdf_saved": False})
            print(f"  no PDF     {title}: {note}")
            return "no PDF on India Code"
        chosen = pick_english_pdf(files)
        if not chosen:
            raise RuntimeError(f"the Act's PDF is over {MAX_PDF_BYTES // (1024 * 1024)} MB: "
                               f"{[(f['name'], round(f['size'] / 1e6)) for f in files][:3]}")
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

        # Another India Code entry of a state Act we already hold (same state, number and year):
        # India Code lists some Acts two to four times (an earlier upload, a "(Single Document)"
        # upload, the Malayalam/Hindi text). One Act, several copies: the copy shown stays as it is
        # and this one is kept as another copy of the same Act; the English entry is always the one
        # shown (it takes over from a Malayalam/Hindi entry saved first).
        entry_state = None if repealed else ic.field(act, "state_name")
        same_act, other_act = None, False
        if not saved and entry_state and entry_state != "CENTRAL":
            with con.cursor() as cur:
                held_same_no = jb_db.state_acts_by_number(cur, entry_state, _int(ic.field(act, "act_number")),
                                                          _int(ic.field(act, "act_year")), act_id_src)
            # the same Act only when the title is too; India Code also gives different Acts one number
            # ("The Mizoram (Market) Regulation (Amendment) Act, 1996" and "The Mizoram Sales Tax
            # (Amendment) Act, 1997" are both its Act 8 of 1996): such an entry is an Act of its own
            same_act = next((h for h in held_same_no if _same_title(h["title"], title, entry_state)), None)
            other_act = bool(held_same_no) and same_act is None
        take_over = bool(same_act) and _not_english(same_act["title"]) and not _not_english(title)
        if same_act and not take_over:
            with con.cursor() as cur:
                if chosen.get("checksum") and chosen["checksum"] in jb_db.version_checksums(cur, same_act["id"]):
                    if not dry_run:
                        with con, con.cursor() as cur2:
                            jb_db.log(cur2, run_id, {**entry, "act_id": same_act["id"], "status": "unchanged",
                                                     "note": f"another India Code entry of {same_act['code']}; "
                                                             "this copy is already kept"})
                    print(f"  unchanged  {title} (another India Code entry of {same_act['code']}, copy already kept)")
                    return "unchanged"
            return save_extra_copy(con, run_id, act, entry, title, chosen, same_act, dry_run)

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
            if not repealed and not state and not saved:
                gz = jb_db.gazette_copy(cur, number, year)
                if gz:
                    notes.append(f"India Code's copy takes over {gz['code']}, loaded earlier from the Gazette of India")
                    print(f"  takes over {gz['code']} ({gz['title']}), loaded earlier from the Gazette of India")
            code = jb_db.act_code(number, year, state, act_id_src)
            if other_act:
                # another Act with the same number on India Code holds that code: this one gets a few
                # characters of its India Code id (its JurisBound number then ends in U001, …)
                code += "-X" + jb_db.sha256(act_id_src.encode())[:6].upper()
                notes.append(f"India Code gives this Act the same number as another Act we hold ({number} of {year}); "
                             "saved as an Act of its own")
            act_row = jb_db.save_act(cur, {
                "code": code, "title": title, "act_number": number, "act_year": year,
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
                "take_over_id": same_act["id"] if take_over else None,
            })
            if take_over:
                # the Malayalam/Hindi copy shown so far stays, as another copy; one copy per date
                fetched = jb_db.free_date(cur, act_row, fetched)
                notes.append(f"English entry; the {same_act['code']} copy saved first (\"{same_act['title'][:60]}\") "
                             "is kept as another copy")
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


def gazette_download(url: str) -> bytes:
    """The Gazette PDF. egazette.gov.in sends its certificate without the issuer's (intermediate)
    certificate, so a plain request cannot check it. The certificate names where its issuer's
    certificate is published; that one is fetched and the site is checked against the usual
    trusted authorities plus it. The check is never switched off."""
    import ssl
    import tempfile
    from urllib.parse import urlparse

    import certifi
    import requests
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    from cryptography.x509.oid import AuthorityInformationAccessOID, ExtensionOID

    try:
        return ic.download(url)
    except Exception as e:                       # India Code's request wraps the SSL error
        if "CERTIFICATE_VERIFY_FAILED" not in str(e):
            raise
    host = urlparse(url).hostname
    cert = x509.load_pem_x509_certificate(ssl.get_server_certificate((host, 443)).encode())
    # follow "issuer published at" up the chain (a new root may itself be cross-signed by a trusted one)
    chain, fetched_from = [], []
    for _ in range(3):
        try:
            aia = cert.extensions.get_extension_for_oid(ExtensionOID.AUTHORITY_INFORMATION_ACCESS).value
        except x509.ExtensionNotFound:
            break
        issuer_url = next((d.access_location.value for d in aia
                           if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS), None)
        if not issuer_url:
            break
        der = requests.get(issuer_url, timeout=60).content
        try:
            cert = x509.load_der_x509_certificate(der)
        except ValueError:
            cert = x509.load_pem_x509_certificate(der)
        chain.append(cert)
        fetched_from.append(issuer_url)
        if cert.issuer == cert.subject:
            break                                 # a self-signed root: the chain ends here
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False, encoding="ascii") as f:
        f.write(Path(certifi.where()).read_text(encoding="ascii", errors="ignore"))
        for c in chain:
            f.write("\n" + c.public_bytes(Encoding.PEM).decode())
        bundle = f.name
    print(f"  note       {host} sends an incomplete certificate chain; its issuers' certificates were fetched "
          f"from {', '.join(fetched_from)}")
    r = requests.get(url, timeout=300, verify=bundle, headers={"User-Agent": ic.USER_AGENT})
    r.raise_for_status()
    return r.content


def load_gazette(con, run_id: str, url: str, number: int, year: int, title: str, dry_run: bool, force: bool) -> str:
    """A new central Act from its Gazette of India PDF, for an Act India Code does not have yet.
    When India Code adds it (the weekly run), India Code's copy takes over this same Act."""
    entry = {"source_act_id": f"GAZETTE:{year}-{number}", "act_title": title, "started_at": datetime.now()}
    try:
        with con.cursor() as cur:
            held = jb_db.central_act_by_number(cur, number, year)
            saved = jb_db.existing_version(cur, entry["source_act_id"])
        if held and not jb_db.gazette_copy(con.cursor(), number, year):
            print(f"  unchanged  {title}: India Code's copy is held ({held['code']}); the Gazette copy is not needed")
            return "unchanged"
        data = gazette_download(url)
        if not data.startswith(b"%PDF"):
            raise RuntimeError("the Gazette link did not give a PDF")
        digest = jb_db.sha256(data)
        if saved and saved["sha"] == digest and not force:
            print(f"  unchanged  {title}")
            return "unchanged"
        print(f"  collected  {title} | Gazette of India | PDF {len(data) / 1e6:.1f} MB | "
              "note: no section records; the pipeline cuts sections from the PDF (Gazette layout)")
        if dry_run:
            return "loaded"
        same_copy = bool(saved and saved["sha"] == digest)
        fetched = saved["as_on"] if same_copy else date.today()
        key = saved["pdf_path"] if same_copy and saved.get("pdf_path") else jb_db.storage_key(None, year, number, title, fetched)
        if not same_copy:
            jb_db.upload_pdf(key, data)
        with con, con.cursor() as cur:
            act_row = jb_db.save_act(cur, {
                "code": jb_db.act_code(number, year), "title": title, "act_number": number, "act_year": year,
                "jurisdiction": "central", "status": "in_force", "source_url": url, "source": "gazette",
                "source_act_id": entry["source_act_id"], "source_item_id": None, "state_name": None,
                "ministry": None, "department": None, "long_title": None, "preamble": None, "enacted_on": None,
                "enforcement_note": "From the Gazette of India; India Code's copy replaces it when India Code adds the Act",
                "subject_type": None, "summary": None,
            })
            jb_db.save_version(cur, act_row, {
                "as_on": fetched, "pdf_path": key, "pdf_sha256": digest, "source_pdf_url": url,
                "section_count": 0, "sections_source": None, "source_checksum": None,
            })
            jb_db.log(cur, run_id, {**entry, "act_id": act_row, "status": "loaded", "pdf_saved": True,
                                    "note": "from the Gazette of India; sections cut by the pipeline"})
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
    ap.add_argument("--all-repealed", action="store_true",
                    help="every Act in the Centre's repealed list (already saved ones are skipped without a download)")
    ap.add_argument("--gazette", metavar="PDF_URL",
                    help="a new central Act from its Gazette of India PDF (India Code does not have it yet); "
                         "needs --act-number, --act-year and --gazette-title")
    ap.add_argument("--act-number", type=int, help="with --gazette: the Act's number (30 for Act 30 of 2025)")
    ap.add_argument("--act-year", type=int, help="with --gazette: the Act's year")
    ap.add_argument("--gazette-title", help='with --gazette: the title, e.g. "The Income-tax Act, 2025"')
    ap.add_argument("--limit", type=int, help="stop after this many Acts")
    ap.add_argument("--dry-run", action="store_true", help="read and count only; save nothing")
    ap.add_argument("--force", action="store_true", help="collect again even when the PDF has not changed")
    ap.add_argument("--skip-saved", action="store_true",
                    help="finish an earlier run: Acts already saved are skipped without asking India Code "
                         "(no change check; the weekly run checks for changes)")
    args = ap.parse_args()

    if args.gazette:
        if not (args.act_number and args.act_year and args.gazette_title):
            ap.error("--gazette needs --act-number, --act-year and --gazette-title")
        run_id = str(uuid.uuid4())
        con = jb_db.connect()
        print(f"run {run_id} | Gazette of India | {'DRY RUN, nothing saved' if args.dry_run else 'saving'}")
        result = load_gazette(con, run_id, args.gazette, args.act_number, args.act_year, args.gazette_title.strip(),
                              args.dry_run, args.force)
        con.close()
        print(f"done: {{'{result}': 1}}")
        return 1 if result == "failed" else 0

    titles = list(args.title)
    if args.titles_file:
        with open(args.titles_file, encoding="utf-8") as f:
            titles += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    if not titles and not args.all_central and not args.repealed and not args.all_repealed and not args.state:
        ap.error("give --title, --titles-file, --all-central, --all-repealed, --state, --repealed or --gazette")

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
    if args.all_repealed:          # after the Acts in force, so an Act held from that list is recognised
        acts += [{**item, "_repealed": True} for item in ic.repealed_acts()]
    if args.limit and not args.state:
        acts = acts[:args.limit]

    run_id = str(uuid.uuid4())
    con = jb_db.connect()
    counts: dict[str, int] = {}
    print(f"run {run_id} | {len(acts)} Acts | {'DRY RUN, nothing saved' if args.dry_run else 'saving'}")
    for act in acts:
        result = load_act(con, run_id, act, args.dry_run, args.force, args.skip_saved)
        counts[result] = counts.get(result, 0) + 1
    if not args.dry_run:
        for jurisdiction, st, seen in whole_lists:
            with con, con.cursor() as cur:
                gone = jb_db.mark_missing(cur, jurisdiction, st, seen)
            for t in gone:
                print(f"  NO LONGER ON INDIA CODE  {t}")
            counts["no longer listed"] = counts.get("no longer listed", 0) + len(gone)
        with con, con.cursor() as cur:
            linked = jb_db.link_repealing_acts(cur)
        if linked:
            print(f"  linked {linked} repealed Acts to the Act that repealed them")
    con.close()
    print(f"done: {counts}")
    return 1 if counts.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
