# Browser Automation Video Recorder, Zoom Service & Impressum Enrichment

## Setup & Configuration

1. Install Python dependencies and Playwright browser:
   ```bash
   pip install -r requirements.txt
   python -m playwright install chromium
   ```

2. Create a `.env` file in the project root (never commit it to git):
   ```env
   DEEPSEEK_API_KEY=your_deepseek_api_key_here
   DEEPSEEK_MODEL=deepseek-chat
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=videos
   DB_USER=video_user
   DB_PASSWORD=video_pass
   ```

3. Start the Docker stack (Apache + Node.js + ffmpeg + Python + PostgreSQL):
   ```bash
   docker compose up --build
   ```

---

## Company CSV Import & Impressum Enrichment (Task 3)

### Web Endpoints

- `GET /upload_companies` — minimal HTML form for uploading `companies.csv` (with a link to `/companies`).
- `POST /upload_companies` — accepts `multipart/form-data` (`;` delimiter, `"` quotechar, UTF-8) and upserts rows by `company_name` without overwriting existing enrichment fields or `status`. Returns `{"imported": N, "updated": M, "total_rows": K}`.
- `GET /companies` — lists all companies as JSON (supports optional `?status=N` filter).
- `GET /companies/<company_name>` — returns a single company record as JSON (`404` if not found).

Example CSV upload via `curl`:
```bash
curl -F "file=@companies.csv" http://127.0.0.1:8000/upload_companies
```

### Enrichment Script (`enrich_companies.py`)

Runs standalone against PostgreSQL (locally or inside the `web` container):

```bash
# Process all non-OK companies (status IS NULL OR status <> 0)
python enrich_companies.py

# Process a single company
python enrich_companies.py --company "nutrimmun GmbH"

# Limit number of rows or filter by status
python enrich_companies.py --limit 5
python enrich_companies.py --status 4
```

Inside the Docker container:
```bash
docker compose exec web python3 enrich_companies.py
```

### Status Codes

| Value | Name | Meaning |
| :--- | :--- | :--- |
| `NULL` | `(pending)` | Imported, not yet processed |
| `0` | `OK` | Impressum parsed and all 4 address fields (`postcode`, `city`, `street`, `house_no`) filled |
| `1` | `NO_IMPRESSUM_LINK_FOUND` | Home page has no Impressum/Imprint link |
| `2` | `PARSING_FAILED` | Impressum parsed, but no fields detected |
| `3` | `ADDRESS_INCOMPLETE` | At least one field detected, but address is incomplete |
| `4` | `ERROR` | Network failure, HTTP 4xx/5xx, or DeepSeek API transport error |
| `5` | `NO_WEBSITE` | `web` column is empty |

### Token Optimization Strategy (`impressum.py`)

Before calling DeepSeek, raw HTML is cleaned (`script`, `style`, `noscript`, `svg`, `nav`, `header`, `footer`, `form` removed, Cloudflare `data-cfemail` decoded) and converted to visible text:

1. **Threshold check:** If `len(text) <= 8000` chars (~2000 tokens), it is sent unchanged.
2. **Boilerplate removal:** Drops navigation noise, cookie-banner phrases, copyright lines, and duplicate top-level menu items.
3. **Head anchor:** Locates the first legal heading (`Impressum`, `Imprint`, `Angaben gemäß § 5 TMG/DDG`, `Anbieter`, `Diensteanbieter`) and trims preceding content.
4. **Tail anchor (sandwich cut):** Locates the *last* strong German legal signal (`+49`/`0049` phone, `DE\d{9}` VAT ID, email, `HRB`/`HRA`/`Registergericht`, `Verantwortlich`/`Geschäftsführer`/`Vertreten durch`) and cuts `400` characters after it, dropping trailing boilerplate.
5. **Verification & fallback:** Verifies the cut retains both a head anchor and at least one strong legal signal (relaxing on failure). If still above the character budget, compacts using keyword-context windows or a head+tail window. Before/after character counts and estimated tokens (`len(text) // 4`) are logged per company.

---

## Video Recorder & Zoom Service (Tasks 1–2)

1. Run the recorder and uploader:
   ```bash
   python capture_video.py --server http://127.0.0.1:8000 --view-seconds 3
   ```

2. Post-process a single video locally:
   ```bash
   python apply_zoom.py --video video/MagentoDev_Berlin.webm --dry-run
   python apply_zoom.py --video video/MagentoDev_Berlin.webm
   ```

3. Trigger manual zoom processing for a job by ID:
   ```bash
   curl -X POST http://127.0.0.1:8000/jobs/1/process
   ```

---

## Tests & Shutdown

Run all unit tests:
```bash
pytest -v
```

Stop containers:
```bash
docker compose down
```