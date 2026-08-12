# POONG — Remote US IT Apply Bot

Windows-local FastAPI + React app: import curated remote US IT jobs, deep-scan JDs, LLM-tailor resume + cover letter PDFs, and assisted-apply via Octo/Chrome CDP with human control.

## Setup (Windows)

```powershell
cd F:\1\Poong-1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium

copy .env.example .env
# Edit .env — set OPENAI_API_KEY, ACCESS_TOKEN, OCTO_PROFILE_UUID, etc.

cd frontend
npm install
npm run build
cd ..

$env:PYTHONPATH = "F:\1\Poong-1\backend"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000/?token=YOUR_ACCESS_TOKEN` (if `ACCESS_TOKEN` is set).

### Optional tunnel

```powershell
.\scripts\deploy_tunnel.bat
```

Use localhost for long deep-scan/tailor batches (Cloudflare tunnels can 524).

## Workflow

1. **Upload** JSON/CSV jobs (or Add job).
2. **Deep-scan** seq range → `jd_ready` (async job + progress poll).
3. **Generate resumes** seq range → resume PDF + cover letter PDF + `answers.json`.
4. **Assisted apply** → UI `window.open` + CDP attach (no blank Playwright tab).
5. Solve bot checks → **Resume**; human confirm / **Mark applied** ends Playwright session.

## Security

- Never commit `.env`, `data/`, or `POONG_FULL_REQUIREMENTS.md`.
- Prefer Octo CDP over `APPLY_ALLOW_LAUNCH_CHROMIUM=true` (launched Chromium has no Greenhouse cookies).
- Rotate keys if leaked from a requirements dump.

## Tests

```powershell
$env:PYTHONPATH = "F:\1\Poong-1\backend"
pytest tests -q
```
