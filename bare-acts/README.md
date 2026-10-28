# Bare acts scraper

Collects Acts from India Code (`https://indiacode.gov.in`), the Government of India's official repository, into the JurisBound database and storage. It only collects: reading the PDF (sections cut from the text, chapters, schedules, search index) is done afterwards by the `acts_cli.py` steps in the **court-pipeline** repository.

Status (28 October 2026): **on Azure** (database schema `jb_kb`, Azure Blob Storage). All central Acts loading; IPC, CrPC and the Evidence Act loaded from the repealed list. Runs from this repository's GitHub workflow (see the main README).

## What it reads

India Code is a DSpace 9 repository with an open data service: no login and no CAPTCHA.

| From | What |
|---|---|
| The Act record | title, Act number, year, date passed, in-force note, ministry, department, long title, preamble, summary |
| Section records (most central Acts) | number, heading, text, footnotes (amendment history) |
| Rule records (Code of Civil Procedure) | rule number, heading, text |
| The Act's English PDF | downloaded and stored; India Code's fingerprint of the file is kept |

Where India Code gives no section records (old repealed central Acts, most state Acts), the pipeline cuts the sections out of the PDF text.

| Collection on India Code | Count (13 Oct 2026) | Loaded by |
|---|---|---|
| Central Acts | 848 | `--all-central` |
| State Acts (36 states and UTs) | 10,529 | `--state NAME` |
| Central "Repeal Acts" (IPC, CrPC, Evidence Act are here) | 4,632 | `--repealed "<title start>"` |

## What it writes

| Where | What |
|---|---|
| `bare_acts` | one row per Act, with its JurisBound number (`JB-ACT-CEN-2023-0000001`, given by a database trigger) |
| `bare_act_versions` | one row per dated copy: PDF location, fingerprints, `text_status = 'pending'` for the pipeline |
| `bare_act_sections` | one row per section or rule (only when India Code gives them) |
| `bare_act_load_log` | one row per Act per run: counts, notes, errors |
| Storage | `acts/central/<year>/act-<number>-<name>/<as-on-date>/english.pdf`; states: `acts/state/<state>/…` |

The tables are in the schema named by `BARE_ACTS_SCHEMA` (default `jb_kb`, Azure), created by the Azure setup SQL. The JurisBound number comes from the database rule on `bare_acts`.

## Run

```
pip install -r requirements.txt          # from this folder
python load.py --titles-file sample_acts.txt --dry-run      # read and check, save nothing, no PDF download
python load.py --titles-file sample_acts.txt                # save
python load.py --title "The Limitation Act, 1963" --force   # reload one Act
python load.py --state Telangana --limit 5                  # first 5 Acts of one state
python load.py --repealed "The Indian Penal Code"           # an Act from the Repeal Acts list
python load.py --all-central                                # all central Acts (not yet run in full)
```

Then, in **court-pipeline**: `python acts_cli.py text` and `python acts_cli.py index`.

Settings come from the repository's `.env`: `SYNC_DATABASE_URL` (Azure, login `jb_court`), `STORAGE_BACKEND=azure`, `AZURE_STORAGE_CONNECTION_STRING`, `SUPABASE_S3_BUCKET` (the container, `court-documents`). Optional `BARE_ACTS_SCHEMA` (default `jb_kb`).

Requests go one at a time with a two-second pause.

## Running it again (monthly)

- **Quick check, no download:** India Code lists a fingerprint of each stored PDF. The same fingerprint as last time means the PDF was not replaced: the Act is logged `unchanged`.
- **A changed PDF** is downloaded and saved as a new copy, marked for the pipeline again.
- **An Act no longer listed** (after a run over a whole list, without `--limit`) gets `source_missing_since` set to the date it was first missed; usually it was repealed.

Log statuses: `loaded`, `unchanged`, `mismatch` (saved, the note says what to look at), `failed` (nothing saved; the note has the error).

## Known limits

- **CrPC:** India Code's PDF is a poor scan; its sections cannot be cut reliably. The Act, its number and PDF are saved, marked `needs_sections`. Needs a cleaner PDF.
- **State Acts:** each state prints differently. Of 15 tested, 10 were cut into sections; 5 (scans, a commentary copy, no list of sections) keep their full text only.
- **State copies of central Acts** (for example the ESI Act listed under Maharashtra) are kept as that state's own Act (JurisBound decision, 28 Oct 2026).
- **Older PDFs print no "as on" date.** The fetch date is kept and marked `as_on_source = 'fetch_date'` (not shown to users).
- **Rules, notifications and circulars linked to an Act are not loaded.**
