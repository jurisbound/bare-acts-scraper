# JurisBound – Bare Acts scraper

Collects India's Acts from **India Code** (`https://indiacode.gov.in`), the Government of India's official repository, into the JurisBound database and storage. It runs on GitHub Actions: every week by itself, and by hand (Run workflow) when needed.

It **only collects**: the Act record, its section records (when India Code has them) and its English PDF. Everything that needs the PDF's text is done afterwards by the JurisBound court pipeline (`acts_cli.py text`, `acts_cli.py index`):

- reading the text (direct read; OCR only for scanned pages);
- cutting sections out of the PDF when India Code gives no section records;
- chapters, schedules and forms, and the "As on" date;
- the search index.

## What is collected

| India Code list | Acts (13 Oct 2026) | Collected |
|---|---|---|
| Central Acts | 848 (830 with section records) | all, weekly |
| Repealed central Acts | 4,632 | all, weekly (a title and a PDF each; sections are cut from the PDF in the pipeline) |
| State and UT Acts (36) | 10,529 | by hand, per state (`bare-acts/states.txt`), after the section cutting is tested |

- **A State's copy of a central Act** is kept as that state's own Act (its own code and JurisBound number, shown under the state). It never replaces the central Act, because a state copy can carry state amendments.
- **Section text:** India Code's section records are the text; the PDF never replaces them. Acts without records get their sections from the PDF in the pipeline.

## How a run works

- **One Act at a time**, with a two-second pause between requests to India Code. One Act is one database transaction.
- **Unchanged Acts are skipped without a download.** India Code lists a fingerprint of each stored PDF, so the weekly run only downloads new or amended Acts.
- **A newly repealed Act** (India Code moves it from the Acts list to the repealed list) is marked repealed on the row we already hold: same JurisBound number, sections and saved items, no second copy.
- **The repealing Act** is read from the repealed title ("Rep., Act 45 of 2023") and linked when we hold it; if it is loaded later, the link is made at the end of a later run.
- **A changed Act** is saved as a new dated copy and marked for the pipeline. It keeps its JurisBound number (`JB-ACT-CEN-2023-0000001`, `JB-ACT-TS-…`), which the database gives.
- **An Act no longer listed** after a full run gets `source_missing_since` (usually repealed). Nothing is deleted.

Every Act of every run is written to `bare_act_load_log` with the status `loaded`, `unchanged`, `mismatch` or `failed`.

## Workflow: `.github/workflows/bare-acts.yml`

| Trigger | What runs |
|---|---|
| Weekly, Sundays 22:00 IST | all central Acts, then all repealed central Acts |
| Run workflow, `what = central` | all central Acts (optional `limit`, `dry_run`) |
| Run workflow, `what = repealed` | all repealed central Acts (saved ones skipped) |
| Run workflow, `what = states` | `states = all` or names from `states.txt` (comma separated); one job per state, 3 at a time |

**Secrets:**
- `AZURE_KB_DATABASE_URL`: Azure Postgres, login `jb_court`, schema `jb_kb`.
- `AZURE_STORAGE_CONNECTION_STRING`: the PDFs go to the container `court-documents` under `acts/…`.

## Run on a computer

```
pip install -r bare-acts/requirements.txt
cp .env.example .env            # fill in the two settings
cd bare-acts
python load.py --titles-file sample_acts.txt --dry-run       # read and count only, save nothing
python load.py --all-central                                 # all central Acts
python load.py --all-repealed
python load.py --state Telangana --limit 10
```

## Folders

| Folder | What |
|---|---|
| `bare-acts/` | the scraper: `india_code.py` (India Code's data service), `jb_db.py` (database and storage), `load.py` (the command) |
| `storage/` | the shared storage module (Azure Blob Storage, or S3) |
| `database/` | the Bare Acts tables as created on Azure (for reference; the live database already has them) |

## Status (28 October 2026)

| What | State |
|---|---|
| Central Acts | all 848 collected on 28 Oct (first run from a laptop). The pipeline's `text` and `index` steps still have to run for them |
| Repealed central Acts | IPC, the Evidence Act and CrPC collected and cut (CrPC from India Code's clean Tripura copy). IPC and the Evidence Act indexed. A full dry run over all ~4,632 was started on 28 Oct (read only); the first real load is the next Sunday run |
| State Acts | not loaded. A Telangana dry run (2 Acts) passed on GitHub |
| Workflow | weekly schedule on; Run workflow tested (repealed and Telangana dry runs green) |

## Pending

1. **Repealed dry run:** check its `FAILED` lines (titles the reader can't parse) and fix them before the first real load.
2. **State Acts plan:**
   - run about 10 Acts each from Telangana, Andhra Pradesh and two more states (Run workflow → `states`, `limit = 10`);
   - run the pipeline's `text` step;
   - check the cut sections against the PDFs and improve the cutting (an earlier test cut only 6 of 15 reliably);
   - then add the states to the weekly schedule and load all 36. Their text and index steps (thousands of PDFs, some needing OCR) should run in the planned Azure pipeline job.
3. ~~CrPC: find a cleaner PDF~~ — done 29 Oct: India Code's Tripura copy of the central text is used (all 534 sections). The Wealth-tax Act 1957 has no cleaner copy.
4. **IPC:** fix the s.4 and s.13 headings, which are cut wrongly. Also one duplicate CPC rule.
5. **Old → new law table:** a draft from the official BPR&D correspondence tables (BNS↔IPC, BNSS↔CrPC, BSA↔Evidence Act), built on the website's side (`court-backend`, Legal data lists). The JurisBound legal team checks it once.
6. **Friendlier Act codes:** new Acts get codes like `A2017-23`; only the first samples have BNS, CPC… They work, but the page addresses are not friendly.
7. **Later, if JurisBound wants them:** India Code's Rules, Notifications, Circulars, Regulations and Orders under each Act.

The full JurisBound pending list (website and backend) is in `court-backend/PLATFORM.md` (private repository).

## Known limits

- **CrPC:** India Code's repealed-list PDF is a poor scan; the saved copy uses India Code's Tripura copy of the same central text instead. A forced reload (`--force`) would bring the poor scan back.
- **State Acts:** most have no section records, and each state prints differently. In an earlier test of 15 State Acts, sections were cut reliably for only some. The cutting is being improved before all states are loaded.
- **Older PDFs print no "As on" date.** The download date is kept in the database but is not shown to users.
- **Rules, notifications and circulars** made under an Act are not collected.
