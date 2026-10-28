"""Database rows and storage for bare acts.

Tables are in schema BARE_ACTS_SCHEMA (default jb_kb, Azure; created by the
Azure setup SQL). PDFs go to the same storage as judgments (Azure Blob Storage
when STORAGE_BACKEND=azure), under their own top folder:

    acts/central/2023/act-45-bharatiya-nyaya-sanhita/2025-10-06/english.pdf
    acts/state/telangana/2017/act-12-<name>/<as-on-date>/english.pdf
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras

_REPO_ROOT = Path(__file__).resolve().parents[1]   # repo root (the folder above bare-acts/)
import sys  # noqa: E402
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))     # for storage/blob_store.py
try:
    from dotenv import load_dotenv

    load_dotenv(_REPO_ROOT / ".env")
except Exception:  # pragma: no cover
    pass


# JurisBound change (Azure, 2026-10-16): the tables used to be named `jb.bare_...`
# (Supabase). Now every query names them without a schema and the connection's
# search_path picks the schema: BARE_ACTS_SCHEMA, default jb_kb (Azure). The
# vector type is in the `extensions` schema on both databases.
_SCHEMA = os.environ.get("BARE_ACTS_SCHEMA", "jb_kb").strip()
if not re.fullmatch(r"[a-z_][a-z0-9_]*", _SCHEMA):
    raise RuntimeError(f"BARE_ACTS_SCHEMA is not a valid schema name: {_SCHEMA!r}")

# Short names lawyers type. Only Acts listed here get one; the rest get "A<year>-<number>".
SHORT_CODES = {
    (45, 2023): "BNS", (46, 2023): "BNSS", (47, 2023): "BSA", (5, 1908): "CPC",
    (26, 1881): "NIA", (35, 2019): "CPA2019", (36, 1963): "LIM", (9, 1872): "ICA",
    (4, 1882): "TPA", (32, 2012): "POCSO",
    (45, 1860): "IPC", (2, 1974): "CRPC", (1, 1872): "IEA",
}


# Category shown on the left of the Bare Acts page. India Code gives none, so it
# is worked out in two steps:
#   1. words in the Act's title (first match wins);
#   2. when the title says nothing ("The Coal Mines (Special Provisions) Act"),
#      the ministry that owns the Act. With titles alone 482 of the 848 central
#      Acts had no category (checked 13 October 2026).
# Nothing matched = "Other". Only a new Act, or one with no category yet, gets
# one, so a category corrected by an admin is never overwritten.
LAW_TYPES = [
    ("Evidence", ["evidence", "sakshya", "oaths"]),
    ("Criminal", ["penal", "nyaya sanhita", "nagarik suraksha", "criminal", "sexual offences", "narcotic",
                  "corruption", "arms act", "explosive", "unlawful activities", "money-laundering",
                  "money laundering", "prisons", "prisoners", "extradition", "juvenile justice", "dowry",
                  "immoral traffic", "police", "fugitive", "terrorist", "atrocities"]),
    ("Constitutional", ["constitution", "citizenship", "representation of the people", "right to information",
                        "human rights", "election", "president", "parliament", "official languages"]),
    ("Family", ["marriage", "divorce", "succession", "adoption", "guardian", "maintenance", "minority",
                "domestic violence", "muslim women", "parsi", "senior citizens"]),
    ("Tax", ["income-tax", "income tax", "goods and services tax", "customs", "excise", "taxation",
             "finance act", "black money", "benami", "cess"]),
    ("Labour", ["labour", "wages", "industrial", "factories", "workmen", "employees", "employment",
                "provident fund", "gratuity", "maternity", "bonus", "trade unions", "social security",
                "occupational safety", "apprentices"]),
    ("Company", ["companies", "insolvency", "bankruptcy", "partnership", "securities", "competition",
                 "limited liability", "chartered accountants", "company secretaries"]),
    ("Banking & Finance", ["banking", "reserve bank", "negotiable instruments", "insurance", "securitisation",
                 "recovery of debts", "payment and settlement", "foreign exchange", "chit funds", "coinage"]),
    ("Property", ["transfer of property", "registration act", "land", "easements", "real estate", "rent",
                  "tenancy", "stamp", "trusts", "benami"]),
    ("Consumer", ["consumer", "legal metrology", "food safety", "essential commodities", "drugs and cosmetics"]),
    ("Environment", ["environment", "pollution", "forest", "wild life", "wildlife", "green tribunal",
                     "biological diversity", "water (prevention"]),
    ("Technology", ["information technology", "telecommunications", "telegraph", "data protection",
                    "aadhaar", "copyright", "patents", "trade marks", "designs act"]),
    ("Transport", ["motor vehicles", "railways", "aircraft", "shipping", "carriage", "highways", "ports"]),
    ("Health", ["health", "medical", "mental", "surrogacy", "transplantation", "epidemic", "pharmacy",
                "nursing", "disabilities", "tobacco"]),
    ("Education", ["education", "university", "universities", "institutes of technology", "school"]),
    ("Civil", ["civil procedure", "limitation", "contract", "specific relief", "arbitration", "mediation",
               "court-fees", "court fees", "suits valuation", "commercial courts", "advocates",
               "legal services", "contempt", "general clauses", "sale of goods", "notaries"]),
]


MINISTRY_TYPES = [
    ("Courts & Legal System", ["law and justice"]),
    ("Government & Administration", ["home affairs", "personnel", "statistics", "north eastern", "parliamentary"]),
    ("Banking & Finance", ["finance"]),
    ("Defence & Security", ["defence"]),
    ("Agriculture & Rural", ["agriculture", "food processing", "cooperation", "rural development", "panchayati",
                             "fisheries", "animal husbandry"]),
    ("Trade & Industry", ["commerce", "textiles", "heavy industries", "micro, small", "steel", "chemicals"]),
    ("Culture & Media", ["culture", "information and broadcasting", "youth affairs", "tourism"]),
    ("Property", ["housing and urban"]),
    ("International", ["external affairs"]),
    ("Mining & Energy", ["petroleum", "power", "coal", "mines", "atomic energy", "renewable"]),
    ("Consumer", ["consumer affairs"]),
    ("Social Welfare", ["women and child", "minority affairs", "social justice", "tribal affairs"]),
    ("Education", ["education", "science and technology", "earth sciences"]),
    ("Transport", ["shipping", "civil aviation", "railways", "road transport"]),
    ("Health", ["health", "ayush"]),
    ("Environment", ["jal sha", "environment"]),
    ("Technology", ["communications", "electronics"]),
    ("Company", ["corporate affairs"]),
    ("Labour", ["labour"]),
]


def law_type(title: str, ministry: str | None = None) -> str:
    t = title.lower()
    for name, words in LAW_TYPES:
        if any(w in t for w in words):
            return name
    m = (ministry or "").lower()
    for name, words in MINISTRY_TYPES:
        if any(w in m for w in words):
            return name
    return "Other"


# Short codes of the states and union territories, as India Code names them. Used in an
# Act's own code ("TS-1988-12") and, through bare_act_state_codes,
# in its JurisBound number ("JB-ACT-TS-1988-0000001").
STATE_CODES = {
    'Andaman and Nicobar Islands': 'AN',
    'Andhra Pradesh': 'AP',
    'Arunachal Pradesh': 'AR',
    'Assam': 'AS',
    'Bihar': 'BR',
    'Chandigarh': 'CH',
    'Chhattisgarh': 'CG',
    'Dadra and Nagar Haveli and Daman and Diu': 'DD',
    'Delhi': 'DL',
    'Goa': 'GA',
    'Gujarat': 'GJ',
    'Haryana': 'HR',
    'Himachal Pradesh': 'HP',
    'Jammu and Kashmir': 'JK',
    'Jharkhand': 'JH',
    'Karnataka': 'KA',
    'Kerala': 'KL',
    'Ladakh': 'LA',
    'Lakshadweep': 'LD',
    'Madhya Pradesh': 'MP',
    'Maharashtra': 'MH',
    'Manipur': 'MN',
    'Meghalaya': 'ML',
    'Mizoram': 'MZ',
    'Nagaland': 'NL',
    'Odisha': 'OD',
    'Puducherry': 'PY',
    'Punjab': 'PB',
    'Rajasthan': 'RJ',
    'Sikkim': 'SK',
    'Tamil Nadu': 'TN',
    'Telangana': 'TS',
    'Tripura': 'TR',
    'Uttar Pradesh': 'UP',
    'Uttarakhand': 'UK',
    'West Bengal': 'WB',
}


def connect():
    url = os.environ.get("SYNC_DATABASE_URL")
    if not url:
        raise RuntimeError("SYNC_DATABASE_URL is not set (see .env.example).")
    # A connection option, not a SET: it survives the rollbacks the steps do.
    return psycopg2.connect(url.replace("postgresql+psycopg2://", "postgresql://"),
                            options=f"-c search_path={_SCHEMA},extensions,public")


def _slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"^the\s+", "", text.lower())
    s = re.sub(r",?\s*\d{4}\s*$", "", s)                 # the year is already a folder
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:limit].strip("-")


def storage_key(state_name: Optional[str], act_year: int, act_number: Optional[int],
                title: str, as_on: date, language: str = "english") -> str:
    scope = "central" if not state_name else f"state/{_slug(state_name)}"
    number = f"act-{act_number}-" if act_number else "act-"
    return f"acts/{scope}/{act_year}/{number}{_slug(title)}/{as_on.isoformat()}/{language}.pdf"


def act_code(act_number: Optional[int], act_year: int, state_name: Optional[str] = None,
             source_act_id: str = "") -> str:
    """Short code of an Act, unique across the Centre and the states.
    Central: "BNS" for the well-known ones, else "A2019-35". State: "TS-1988-12";
    a state Act with no number gets a few characters of its India Code id instead."""
    if not state_name:
        return SHORT_CODES.get((act_number, act_year)) or f"A{act_year}-{act_number or 0}"
    st = STATE_CODES.get(state_name) or re.sub(r"[^A-Za-z]", "", state_name)[:6].upper()
    tail = str(act_number) if act_number else "X" + hashlib.sha256(source_act_id.encode()).hexdigest()[:6].upper()
    return f"{st}-{act_year}-{tail}"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def upload_pdf(key: str, data: bytes) -> None:
    # JurisBound change (Azure): the shared storage module (storage/blob_store.py),
    # as the court scrapers -- Azure Blob Storage when STORAGE_BACKEND=azure.
    from storage.blob_store import storage_client

    if not os.environ.get("SUPABASE_S3_BUCKET"):
        raise RuntimeError("Storage setting missing: SUPABASE_S3_BUCKET (the container name)")
    client = storage_client()
    client.put_object(Bucket=os.environ["SUPABASE_S3_BUCKET"], Key=key, Body=data,
                      ContentType="application/pdf")


def existing_version(cur, source_act_id: str) -> Optional[dict]:
    """The newest saved copy of this Act, if any."""
    cur.execute(
        """select v.id, v.consolidated_as_of, v.pdf_sha256, v.pdf_path, v.source_checksum
             from bare_acts a join bare_act_versions v on v.act_id = a.id
            where a.source_act_id = %s order by v.fetched_at desc nulls last, v.consolidated_as_of desc limit 1""",
        (source_act_id,),
    )
    row = cur.fetchone()
    return {"id": row[0], "as_on": row[1], "sha": row[2], "pdf_path": row[3], "checksum": row[4]} if row else None


def remember_checksum(cur, version_id: str, checksum: Optional[str]) -> None:
    cur.execute("update bare_act_versions set source_checksum = %s where id = %s", (checksum, version_id))


def mark_missing(cur, jurisdiction: str, state_name: Optional[str], seen_ids: set[str]) -> list[str]:
    """After a run over a whole list: Acts we hold from that list that India Code no longer
    shows get the date they were first missed (usually: repealed); those back again are cleared.
    Returns the titles newly missing."""
    cur.execute(
        """select id, source_act_id, title, source_missing_since from bare_acts
            where source = 'india_code' and source_act_id not like 'REPEAL:%%'
              and jurisdiction = %s and state_name is not distinct from %s""", (jurisdiction, state_name))
    newly = []
    for act_id, src, title, since in cur.fetchall():
        if src in seen_ids and since is not None:
            cur.execute("update bare_acts set source_missing_since = null where id = %s", (act_id,))
        elif src not in seen_ids and since is None:
            cur.execute("update bare_acts set source_missing_since = current_date where id = %s", (act_id,))
            newly.append(title)
    return newly


def save_act(cur, a: dict) -> str:
    """Insert or update the Act row; returns its id.

    A row with the same short code and no India Code id is one of the sample
    rows the pages were built with: it is taken over (its sample sections go
    when its old copy is replaced), so its short names and old/new links stay.
    """
    cur.execute("select id from bare_acts where source_act_id = %s", (a["source_act_id"],))
    row = cur.fetchone()
    if not row:
        cur.execute("select id from bare_acts where code = %s and source_act_id is null", (a["code"],))
        row = cur.fetchone()
        if row:
            cur.execute("delete from bare_act_versions where act_id = %s", (row[0],))   # sample copy + sections
    a = {"replaced_by_code": None, **a}
    cols = ["code", "title", "act_number", "act_year", "jurisdiction", "status", "replaced_by_code", "source_url", "source",
            "source_act_id", "source_item_id", "state_name", "ministry", "department", "long_title",
            "preamble", "enacted_on", "enforcement_note", "subject_type", "summary"]
    a = {**a, "category": law_type(a["title"], a.get("ministry"))}
    if row:
        sets = ", ".join(f"{c} = %({c})s" for c in cols if c != "code")
        cur.execute(
            f"update bare_acts set {sets}, category = coalesce(category, %(category)s), "
            # a sample row taken over has no JurisBound number yet; a row that has one keeps it
            "doc_number = coalesce(doc_number, next_act_number(%(state_name)s, %(act_year)s)), updated_at = now() "
            "where id = %(id)s", {**a, "id": row[0]})
        return row[0]
    cols = cols + ["category"]
    cur.execute(
        f"insert into bare_acts ({', '.join(cols)}) values ({', '.join(f'%({c})s' for c in cols)}) returning id", a)
    return cur.fetchone()[0]


def save_version(cur, act_id: str, v: dict) -> str:
    """One copy of the Act (one PDF). The date is the day it was fetched; the
    pipeline replaces it with the "as on" date printed in the PDF. Saving the
    same copy again replaces its sections and sends it through the pipeline again."""
    cur.execute(
        """insert into bare_act_versions
             (act_id, consolidated_as_of, as_on_source, status, pdf_path, pdf_sha256, source_pdf_url,
              section_count, sections_source, source_checksum, text_status, index_status, fetched_at)
           values (%(act_id)s, %(as_on)s, 'fetch_date', 'published', %(pdf_path)s, %(pdf_sha256)s,
                   %(source_pdf_url)s, %(section_count)s, %(sections_source)s, %(source_checksum)s,
                   'pending', null, now())
           on conflict (act_id, consolidated_as_of) do update set
              pdf_path = excluded.pdf_path, pdf_sha256 = excluded.pdf_sha256,
              source_pdf_url = excluded.source_pdf_url, section_count = excluded.section_count,
              sections_source = excluded.sections_source, source_checksum = excluded.source_checksum,
              text_status = 'pending', text_note = null,
              index_status = null, fetched_at = now()
           returning id""",
        {**v, "act_id": act_id},
    )
    version_id = cur.fetchone()[0]
    cur.execute("delete from bare_act_sections where version_id = %s", (version_id,))
    cur.execute("delete from bare_act_parts where version_id = %s", (version_id,))
    cur.execute(
        "update bare_act_versions set status = 'superseded' where act_id = %s and id <> %s and status = 'published'",
        (act_id, version_id),
    )
    return version_id


def save_sections(cur, version_id: str, rows: list[dict]) -> int:
    """Returns how many were saved; a repeated number under the same parent is skipped."""
    saved = 0
    for r in rows:
        cur.execute(
            """insert into bare_act_sections
                 (version_id, section_no, heading, body, chapter, sort, kind, parent,
                  source_section_id, body_html, footnotes, pdf_page, repealed)
               values (%(version_id)s, %(section_no)s, %(heading)s, %(body)s, %(chapter)s, %(sort)s,
                       %(kind)s, %(parent)s, %(source_section_id)s, %(body_html)s, %(footnotes)s, %(pdf_page)s,
                       %(repealed)s)
               on conflict (version_id, kind, parent, section_no) do nothing""",
            {**r, "version_id": version_id},
        )
        saved += cur.rowcount
    return saved


def log(cur, run_id: str, entry: dict) -> None:
    base = {"source_act_id": None, "act_title": None, "act_id": None, "sections_in_source": None,
            "sections_in_pdf": None, "sections_saved": None, "parts_saved": None, "pdf_saved": None,
            "note": None, "started_at": None}
    cur.execute(
        """insert into bare_act_load_log
             (run_id, source_act_id, act_title, act_id, status, sections_in_source, sections_in_pdf,
              sections_saved, parts_saved, pdf_saved, note, started_at, finished_at)
           values (%(run_id)s, %(source_act_id)s, %(act_title)s, %(act_id)s, %(status)s, %(sections_in_source)s,
                   %(sections_in_pdf)s, %(sections_saved)s, %(parts_saved)s, %(pdf_saved)s, %(note)s,
                   coalesce(%(started_at)s, now()), now())""",
        {**base, **entry, "run_id": run_id},
    )
