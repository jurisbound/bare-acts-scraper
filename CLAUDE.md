# CLAUDE.md

JurisBound's Bare Acts scraper: collects Acts from India Code (indiacode.gov.in) into Azure Postgres (schema `jb_kb`, login `jb_court`) and Azure Blob Storage (container `court-documents`, `acts/...`). See README.md.

- Collect only. PDF text, OCR, section cutting and the search index belong to the court pipeline (`acts_cli.py text` / `index`); do not add them here.
- Always call the client JurisBound. Never name or link other open-source scraper projects.
- Keep India Code's two-second pause and one request at a time; at most 3 state jobs in parallel.
- Never delete an Act row: a reload with `--force` keeps its JurisBound number. A missing Act only gets `source_missing_since`.
- India Code section records are the section text; never replace them with PDF text.
- Decisions (28 Oct 2026): state copies of central Acts stay as that state's own Act; only IPC, CrPC and the Evidence Act from the repealed list for now; states are loaded after the section cutting is tested.
- Secrets live in GitHub (`AZURE_KB_DATABASE_URL`, `AZURE_STORAGE_CONNECTION_STRING`) or a local `.env`; never print or commit them.
