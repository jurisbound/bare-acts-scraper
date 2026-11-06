# JurisBound – Bare Acts scraper

Collects India's Acts from **India Code** (`https://indiacode.gov.in`), the Government of India's official repository, into the JurisBound database and storage. It runs on GitHub Actions by itself (central Acts weekly, repealed Acts monthly), and by hand (Run workflow) when needed.

It **only collects**: the Act record, its section records (when India Code has them) and its English PDF. Everything that needs the PDF's text is done afterwards by the JurisBound court pipeline (`acts_cli.py text`, `acts_cli.py index`):

- reading the text (direct read; OCR only for scanned pages);
- cutting sections out of the PDF when India Code gives no section records;
- chapters, schedules and forms, and the "As on" date;
- the search index.

## What is collected

| India Code list | Acts (13 Oct 2026) | Collected |
|---|---|---|
| Central Acts | 848 (830 with section records) | all, weekly |
| Repealed central Acts | 4,632 | all, monthly (a title and a PDF each; sections are cut from the PDF in the pipeline) |
| State and UT Acts (36) | 10,544 entries = 9,763 Acts (781 entries are extra copies of an Act) | weekly, with the central Acts; also by hand, per state (`bare-acts/states.txt`) |

- **A State's copy of a central Act** is kept as that state's own Act (its own code and JurisBound number, shown under the state). It never replaces the central Act, because a state copy can carry state amendments.
- **Section text:** India Code's section records are the text; the PDF never replaces them. Acts without records get their sections from the PDF in the pipeline.

## How a run works

- **One Act at a time**, with a two-second pause between requests to India Code. One Act is one database transaction.
- **Unchanged Acts are skipped without a download.** India Code lists a fingerprint of each stored PDF, so a run only downloads new or amended Acts.
- **A newly repealed Act** (India Code moves it from the Acts list to the repealed list) is marked repealed on the row we already hold: same JurisBound number, sections and saved items, no second copy.
- **The repealing Act** is read from the repealed title ("Rep., Act 45 of 2023") and linked when we hold it; if it is loaded later, the link is made at the end of a later run.
- **A repealed Act's own number** is read from India Code's title. India Code's titles carry some typos ("The Indian Patents and Designs (Amendment) Act, 1930, 7 of 1938"); the PDF's file name has the number printed in the Act ("A1930-7.pdf": "ACT No. VII OF 1930"). When the two disagree and the file's year is the year in the Act's name (or the next one: an Act passed late in a year is numbered in the next), the file's number is used. An Act in force is never marked repealed because of another Act's number: the titles must match (a typo once marked the Coast Guard Act, 1978 repealed). An Act India Code lists twice is skipped ("listed twice"); two different Acts given one number are both kept, the second with a code of its own and a note to check the number in its PDF.
- **A changed Act** is saved as a new dated copy and marked for the pipeline. It keeps its JurisBound number, which the database gives from the Act's official number: Hindu Marriage Act, 1955 (Act 25 of 1955) = `JB-ACT-CEN-1955-0025`, Telangana Act 12 of 2019 = `JB-ACT-TS-2019-0012`; an Act without a usable number gets `JB-ACT-…-U001`.
- **The same state Act listed more than once** (India Code lists some Acts two to four times: an earlier upload, a "(Single Document)" upload, the Malayalam/Hindi text): one Act. The other entries' PDFs are kept as further copies of that Act (not shown; the copy shown stays the same), and an English entry is always the copy shown.
- **An entry without a PDF** holds only the state's rules, notifications or orders under an Act (mostly a central Act listed under the state), or only a title: logged as "no Act PDF on India Code", not as a failure.
- **An Act no longer listed** after a full run gets `source_missing_since` (usually repealed). Nothing is deleted.

Every Act of every run is written to `bare_act_load_log` with the status `loaded`, `unchanged`, `mismatch` or `failed`.

## Workflow: `.github/workflows/bare-acts.yml`

| Trigger | What runs |
|---|---|
| Weekly, Sundays 22:00 IST | all central Acts, and every state's Acts (one job per state, 5 at a time) |
| Monthly, the 2nd at 00:00 IST | all repealed central Acts |
| Run workflow, `what = central` | all central Acts (optional `limit`, `dry_run`) |
| Run workflow, `what = repealed` | all repealed central Acts (saved ones skipped) |
| Run workflow, `what = states` | `states = all` or names from `states.txt` (comma separated); one job per state, 5 at a time |

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
# a new Act India Code does not have yet, from its Gazette of India PDF
python load.py --gazette https://egazette.gov.in/WriteReadData/2025/265620.pdf --act-number 30 --act-year 2025 --gazette-title "The Income-tax Act, 2025"
```

**Gazette Acts.** A new Act appears in the Gazette before India Code has it. `--gazette` saves it as a central Act with `source = 'gazette'`; the pipeline's `text` step cuts its sections from the Gazette layout (headings in the margin). When India Code later lists the same Act (same number and year), the weekly run updates that same row instead of adding a second one, so its code, saved sections and links stay. egazette.gov.in sends an incomplete certificate chain; the loader fetches the missing issuer certificates and still checks the site's certificate in full.

## Folders

| Folder | What |
|---|---|
| `bare-acts/` | the scraper: `india_code.py` (India Code's data service), `jb_db.py` (database and storage), `load.py` (the command) |
| `storage/` | the shared storage module (Azure Blob Storage, or S3) |
| `database/` | the Bare Acts tables as created on Azure (for reference; the live database already has them): `01_bare_acts_tables.sql` the tables, `02_act_numbers_official.sql` JurisBound numbers from the official Act number, `03_act_layout.sql` the whole-Act layout column (the court pipeline fills it) |

## Status (6 November 2026)

| What | State |
|---|---|
| Central Acts in force | 845 held, collected weekly. Text, sections and page view done in the court pipeline |
| Repealed central Acts | 4,627 held, checked monthly. Act numbers checked against India Code's PDF file names (14 corrected). 360 through the pipeline's text step, about 4,270 waiting |
| State and UT Acts | 9,535 held (35 states/UTs with Acts), collected weekly. About 9,500 wait for the pipeline's text step, which a sample test of 260 Acts showed cuts about two in three English Acts reliably |
| Workflow | weekly (central + states, 5 states at a time) and monthly (repealed) schedules on; Run workflow tested |
| Old → new section tables | drafts for IPC/CrPC/Evidence Act → BNS/BNSS/BSA, Income-tax 1961 → 2025 and Companies 1956 → 2013, in court-backend; waiting for the JurisBound legal team's check |

The text, sections, page view and search index of every Act are done by the court pipeline: see `BARE_ACTS.md` in `court-pipeline` (setup, splitting the work between people, where the work stands).

## Development

Work on the `develop` branch and merge to `main` when a change is tested: the scheduled runs use `main`. Run a change first as a dry run (`--dry-run`, or Run workflow with `dry_run`), then on one state with a `limit`.

## Later (decisions still open)

1. **State rules, notifications and orders:** India Code holds them under many Acts (for example Delhi's Central Sales Tax Rules, 2005). Not collected.
2. **11 repealed Acts** have no number in their PDF file name, so their number could not be checked that way.
3. **Regional-language Acts** (Hindi, Odia, Malayalam …) and Hindi copies labelled as English: counted after the pipeline's full run, then decided.
4. **Friendlier Act codes:** new Acts get codes like `A2017-23`; they work, but page addresses are not friendly.
5. **Rules, Notifications, Circulars, Regulations and Orders under central Acts**, if JurisBound wants them.

The full JurisBound pending list (website and backend) is in `court-backend/PLATFORM.md` (private repository).

## Known limits

- **CrPC:** India Code's repealed-list PDF is a poor scan; the saved copy uses India Code's Tripura copy of the same central text instead. A forced reload (`--force`) would bring the poor scan back.
- **State Acts:** most have no section records, and each state prints differently. Sections are cut from the PDF by the court pipeline only when the cut passes its checks; poor scans and Hindi-only PDFs keep their whole text and a page view.
- **Older PDFs print no "As on" date.** The download date is kept in the database but is not shown to users.
- **Rules, notifications and circulars** made under an Act are not collected.
