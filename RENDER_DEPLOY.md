# Deploying DROP to Render

## What changed in this package

- **`config.py`** — normalizes `postgres://` → `postgresql://` (Render's Postgres URLs need this), adds `pool_pre_ping` so idle DB connections don't crash requests, and adds secure cookie settings for HTTPS.
- **`app.py`** — added `ProxyFix` (so Flask correctly detects HTTPS behind Render's proxy), and the `if __name__ == "__main__"` block now respects Render's `PORT` env var and turns debug mode off in production.
- **`requirements.txt`** — added `gunicorn` (production server) and `psycopg2-binary` (Postgres driver).
- **`Procfile`** — tells Render how to start the app.
- **`render.yaml`** — optional one-click blueprint (skip this if you'd rather click through the dashboard manually).

## 1. Push this to GitHub

Render deploys from a Git repo, so this whole folder needs to be committed and pushed (replace your existing `app.py`, `config.py`, `requirements.txt` with these versions, and add the new `Procfile`).

## 2. Create the Postgres database first

In the Render dashboard: **New → PostgreSQL** → give it a name (e.g. `drop-db`) → free plan is fine for now → Create Database. Once it's up, copy the **Internal Database URL** — you'll need it in step 3.

## 3. Create the web service

**New → Web Service** → connect your GitHub repo → Render should auto-detect Python. Set:

| Setting | Value |
|---|---|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `gunicorn app:app --workers 2 --threads 4 --timeout 120` |
| Instance Type | Free (or Starter, to avoid cold starts) |

> The `--timeout 120` matters: gunicorn's default worker timeout is 30 seconds, and DROP's AI course-generation calls to Groq can take longer than that. Without this, you'll get intermittent 502s on longer AI requests.

## 4. Set environment variables

In the web service's **Environment** tab, add:

| Key | Value |
|---|---|
| `SECRET_KEY` | a long random string (Render can auto-generate this) |
| `DATABASE_URL` | the Internal Database URL from step 2 |
| `XAI_API_KEY` | your Groq API key |
| `XAI_BASE_URL` | `https://api.groq.com/openai/v1` |
| `GROK_MODEL` | `openai/gpt-oss-120b` |
| `AI_MOCK_FALLBACK` | `1` (keeps the app working end-to-end even if the AI call ever fails or the key isn't set) |

Render automatically sets a `RENDER` variable for you — that's what the updated `config.py` uses to detect production and turn on secure cookies, so you don't need to set that one yourself.

## 5. Deploy

Click **Create Web Service**. Render will build and deploy; watch the logs for `[DROP] Using database: postgresql://...` to confirm it picked up Postgres instead of falling back to SQLite.

## Things to know about Render's free tier

- **Cold starts**: the free tier spins the service down after ~15 minutes of inactivity. The first request after that will take 30–50 seconds to wake up. This is a plan limitation, not a bug — upgrading to the Starter plan ($7/mo) removes it.
- **Ephemeral disk**: anything saved to `UPLOAD_FOLDER` (student-uploaded PDFs/docs for solo study sessions) is **not persistent** — it's wiped on every redeploy or restart. For now that's fine for a demo, but before real users rely on uploaded files surviving, you'll want to point `extract_text_from_upload` at S3, Cloudinary, or Render's own persistent disk add-on instead of local disk.
- **Free Postgres expires**: Render's free Postgres databases are deleted after 90 days of the free trial. Fine for a hackathon demo, but you'll need to upgrade to a paid instance before this goes live for real.
- **`db.create_all()` on every boot**: this creates tables that don't exist yet but won't handle future schema changes. Once you need to alter existing tables in production, you'll want Flask-Migrate/Alembic instead of relying on `create_all()`.

## Quick local test before deploying

```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://user:pass@localhost:5432/drop"  # or leave unset to use SQLite
gunicorn app:app --workers 2 --threads 4 --timeout 120
```

If that runs cleanly on your machine, it'll run cleanly on Render.
