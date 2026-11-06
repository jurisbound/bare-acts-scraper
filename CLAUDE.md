# CLAUDE.md

JurisBound's Bare Acts scraper: collects Acts from India Code (indiacode.gov.in) into Azure Postgres (schema `jb_kb`, login `jb_court`) and Azure Blob Storage (container `court-documents`, `acts/...`). User guide: README.md. Last updated: 6 November 2026.

## Rules

- Collect only. PDF text, OCR, section cutting, the page view and the search index belong to the court pipeline (`court-pipeline`, `acts_cli.py text` / `layout` / `index`, guide `BARE_ACTS.md` there); do not add them here.
- Always call the client JurisBound. Never name or link other open-source scraper projects.
- Keep India Code's two-second pause and one request at a time per job; at most 5 state jobs in parallel.
- Never delete an Act row, a copy or a PDF in storage (also not the ~690 extra PDFs the first state run left). A reload with `--force` keeps the JurisBound number. An Act no longer listed only gets `source_missing_since`.
- India Code section records are the section text; never replace them with PDF text.
- Secrets live in GitHub (`AZURE_KB_DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`) or a local `.env`; never print or commit them.
- Commits are authored as Mahesh-Prabha (local git config). Work on the `develop` branch; `main` is what the scheduled runs use, so merge to `main` only when a change is tested.
- Explain a change and get the user's OK before making it; check facts on real data (the PDF, the database) before stating them.

## Decisions (do not reopen without the user)

- State copies of central Acts stay as that state's own Act; they never replace the central Act.
- All repealed central Acts are collected (monthly, the 2nd); central and all states weekly (Sunday 22:00 IST, 5 states at a time).
- The same state Act listed several times: one Act, the other entries kept as further copies; an English entry is always the copy shown. Same number but a different title: its own Act (code `-X…`).
- Entries without a PDF are logged "no Act PDF on India Code" (they hold state rules/notifications or only a title), not as failures.
- Big PDFs: download up to 350 MB, files over 20 MB stored as a smaller reading copy. No further limit change needed.
- JurisBound number from the official Act number (`JB-ACT-CEN-1955-0025`), `U001…` when there is none. Existing numbers are kept.
- Repealed Acts: the number comes from India Code's PDF file name when the title has a typo and the years agree; an in-force Act is never marked repealed by another Act's number (titles must match); an Act listed twice is skipped. 14 numbers were corrected in the database on 4–5 Nov 2026 after checking each PDF.
- Section-by-section old → new tables only for the big rewrites (built in court-backend); other repealed Acts get only the "repealed by Act X" link from the title.

## State (6 November 2026)

- Held: 845 central Acts in force, 4,627 repealed central Acts, 9,535 state Acts (35 states/UTs with Acts). The text step of the court pipeline has run for about 1,250 copies; about 13,760 wait for it.
- Last pushed: `3314968` (layout column), `df4cf26` (repealed numbers). Verified on GitHub.

## Later (decisions still open)

- Collect the states' rules, notifications and orders that India Code holds under Acts.
- 11 repealed Acts with no number in their PDF file name: numbers not checked.
- Regional-language Acts and Hindi copies labelled as English: count after the pipeline's big run, then decide.
