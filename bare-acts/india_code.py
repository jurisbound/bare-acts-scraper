"""Read-only client for India Code (https://indiacode.gov.in).

The site is a DSpace 9 repository. Its data service needs no login and has no
CAPTCHA. Every Act is one record, and every section, rule, schedule title and
appendix title is its own record linked to the Act by `dc.identifier.act_id`.

Checked on 13 October 2026:
  * a search by title also returns state copies of central Acts, so central
    Acts are picked by `state_name == CENTRAL`, never by title alone;
  * repealed central Acts (IPC, CrPC, Indian Evidence Act) are not in the Acts list but in the
    Centre's "Repeal Acts" list, as a title and a PDF only (no section records);
  * `dc.date.last_modified` is not a safe change signal (the CPC record says
    2019 while its PDF says "As on the 10th January, 2026"), so the caller
    compares the PDF itself.
"""
from __future__ import annotations

import html
import re
import time
from typing import Iterator, Optional

import requests

BASE = "https://indiacode.gov.in/server/api"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
PAUSE_SECONDS = 2.0      # one request at a time, with a pause; the site is a public service
PAGE_SIZE = 100
TIMEOUT = 120

_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT})


def _get(url: str, params: Optional[dict] = None, raw: bool = False):
    last = None
    for attempt in range(3):
        try:
            r = _session.get(url, params=params, timeout=TIMEOUT,
                             headers={"Accept": "*/*" if raw else "application/json"})
            r.raise_for_status()
            time.sleep(PAUSE_SECONDS)
            return r.content if raw else r.json()
        except Exception as e:  # network drop, 5xx
            last = e
            time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"India Code request failed after 3 tries: {url} ({last})")


def field(record: dict, name: str) -> Optional[str]:
    """First value of a metadata field, e.g. field(r, "act_id")."""
    for key in (f"dc.identifier.{name}", f"dc.date.{name}", f"dc.{name}", name):
        v = record["metadata"].get(key)
        if v:
            return v[0]["value"]
    return None


def fields(record: dict, name: str) -> list[str]:
    v = record["metadata"].get(f"dc.identifier.{name}") or record["metadata"].get(f"dc.{name}") or []
    return [x["value"] for x in v]


def search(query: str, collection: Optional[str] = None) -> Iterator[dict]:
    """Yield every record matching `query` (optionally one record type)."""
    page = 0
    while True:
        params = {"query": query, "size": PAGE_SIZE, "page": page}
        if collection:
            params["f.identifier_collection"] = f"{collection},equals"
        result = _get(f"{BASE}/discover/search/objects", params)["_embedded"]["searchResult"]
        for obj in result.get("_embedded", {}).get("objects", []):
            yield obj["_embedded"]["indexableObject"]
        page += 1
        if page >= result["page"]["totalPages"]:
            return


def central_acts() -> Iterator[dict]:
    """Every central Act record."""
    yield from search("dc.identifier.state_name:CENTRAL", collection="ACT")


_collections: dict[str, dict[str, str]] = {}      # group name -> {list name -> id}


def collection(group: str, name: str) -> str:
    """Id of one list of one group. Groups are "CENTRAL" and the 36 states and union
    territories; each has its own lists: "Acts", "Repeal Acts", "Spent Acts", "Rule", …"""
    if not _collections:
        for c in _get(f"{BASE}/core/communities/search/top", {"size": 60})["_embedded"]["communities"]:
            _collections[c["name"]] = {"_id": c["uuid"]}
    if group not in _collections:
        raise RuntimeError(f"India Code has no group named '{group}'. Groups: {', '.join(sorted(_collections))}")
    lists = _collections[group]
    if len(lists) == 1:
        cols = _get(f"{BASE}/core/communities/{lists['_id']}/collections", {"size": 60})
        for col in cols["_embedded"]["collections"]:
            lists[col["name"]] = col["uuid"]
    return lists[name]


def central_collection(name: str) -> str:
    return collection("CENTRAL", name)


def state_acts(state: str) -> Iterator[dict]:
    """Every Act in one state's (or union territory's) own list of Acts."""
    scope = collection(state, "Acts")
    page = 0
    while True:
        result = _get(f"{BASE}/discover/search/objects", {"scope": scope, "size": PAGE_SIZE, "page": page,
                                                         "sort": "dc.title,ASC"})["_embedded"]["searchResult"]
        for obj in result.get("_embedded", {}).get("objects", []):
            yield obj["_embedded"]["indexableObject"]
        page += 1
        if page >= result["page"]["totalPages"]:
            return


def find_repealed_act(title_start: str) -> Optional[dict]:
    """A repealed central Act, by the start of its title as India Code writes it,
    e.g. "The Indian Penal Code, 45 of 1860"."""
    want = _title_key(title_start)
    data = _get(f"{BASE}/discover/search/objects",
                {"scope": central_collection("Repeal Acts"), "query": f'dc.title:"{title_start}"', "size": 20})
    for obj in data["_embedded"]["searchResult"].get("_embedded", {}).get("objects", []):
        item = obj["_embedded"]["indexableObject"]
        if _title_key(item["name"]).startswith(want):
            return item
    return None


def find_central_act(title: str) -> Optional[dict]:
    """The central Act whose title matches exactly (case and 'The' ignored)."""
    want = _title_key(title)
    for act in search(f'dc.title:"{title}"', collection="ACT"):
        if field(act, "state_name") == "CENTRAL" and _title_key(act["name"]) == want:
            return act
    return None


def _title_key(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"^\s*the\s+", "", t.lower())).strip()


def act_records(act_id: str) -> dict[str, list[dict]]:
    """Every record linked to one Act, grouped by record type
    (ACT, SECTION, SCHORDRULE, SCHEDULEORDER, SCHEDULE, APPENDIX, ANNEXURE, …)."""
    groups: dict[str, list[dict]] = {}
    for rec in search(f'dc.identifier.act_id:"{act_id}"'):
        groups.setdefault(field(rec, "collection") or "?", []).append(rec)
    for recs in groups.values():
        recs.sort(key=lambda r: _int(field(r, "order_number")))
    return groups


def act_files(item_uuid: str) -> list[dict]:
    """The Act's original files: name, size in bytes, download address."""
    out = []
    data = _get(f"{BASE}/core/items/{item_uuid}/bundles", {"embed": "bitstreams"})
    for bundle in data["_embedded"]["bundles"]:
        if bundle["name"] != "ORIGINAL":
            continue
        for b in bundle.get("_embedded", {}).get("bitstreams", {}).get("_embedded", {}).get("bitstreams", []):
            out.append({"name": b["name"], "size": b["sizeBytes"], "url": b["_links"]["content"]["href"],
                        # fingerprint of the file as India Code stores it; changes when they replace the PDF
                        "checksum": (b.get("checkSum") or {}).get("value")})
    return out


def download(url: str) -> bytes:
    return _get(url, raw=True)


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


_BREAK = re.compile(r"<br\s*/?>|<hr[^>]*>|</p>|</div>|</tr>|</center>|</li>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_to_text(value: Optional[str]) -> str:
    """Section text as published is simple HTML; keep its line breaks."""
    if not value:
        return ""
    t = _BREAK.sub("\n", value)
    t = html.unescape(_TAG.sub("", t)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)
