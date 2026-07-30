# DROP — The AI Teacher That Never Stops Teaching

A Flask-based AI education platform. Teachers create classrooms and Grok (xAI)
generates the entire course — weeks, lessons, assignments, weekly tests,
midterms, and a final exam. Students learn through AI-taught lessons, get
instantly auto-graded with a "why was this wrong" understanding engine, chat
with an AI tutor, study solo from any topic or document, and level up through
XP, streaks, and achievements.

There is no landing page — the app starts at `/login` or `/signup`.

## Quick start

```bash
cd drop
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and add your XAI_API_KEY (get one at https://console.x.ai)

python app.py
```

Visit **http://localhost:5000** — you'll land on `/login`, redirecting to
`/signup` if you don't have an account yet.

## Running without a Grok API key

If `XAI_API_KEY` is left blank, DROP automatically falls back to structured
mock content (`AI_MOCK_FALLBACK=1` in `.env`) so every flow — course
generation, lessons, quizzes, grading, and the tutor — still works end to
end for a demo. Add a real key any time to switch to live Grok output; no
code changes needed.

## Project structure

```
app.py            All Flask routes (per spec — routing lives in one file)
config.py         App + Grok/xAI configuration
extensions.py     Flask-SQLAlchemy, Flask-Login, Flask-Bcrypt singletons
models.py         SQLAlchemy models (users, classrooms, lessons, etc.)
ai_engine.py       Grok integration: course generation, tutor, auto-grading, insights
templates/        Jinja2 templates (auth, teacher, student, shared)
static/           CSS + JS
uploads/          Uploaded syllabi / study documents
instance/         SQLite database (created automatically)
```

## Key flows

- **Teacher**: sign up → create classroom (name, subject, weeks, target
  grade, optional syllabus) → Grok generates the full curriculum
  synchronously → share the join code → review analytics, grade or
  auto-grade submissions, post announcements.
- **Student**: sign up → join a classroom by code (or start a Solo Study
  session from a topic/PDF) → work through AI-taught lessons (objectives,
  notes, examples, common mistakes, practice, revision, homework, mini
  quiz) → submit assignments/tests for instant Grok grading with
  misconception detection → chat with the AI Tutor in different modes
  (simplify, ELI10, visual, mathematical, more examples) → track XP,
  level, streaks, and achievements.

## Notes

- SQLite is used by default (`instance/drop.db`); swap `DATABASE_URL` in
  `.env` for Postgres/MySQL later — no code changes needed since
  SQLAlchemy handles the abstraction.
- Course generation currently runs synchronously on classroom creation.
  For a production deployment, move `ai.generate_course(...)` into a
  background worker (Celery/RQ) and poll `classroom.ai_status`.
- This is a complete, working reference implementation covering the full
  spec's core product loop. A few of the more exhaustive spec items
  (e.g. attendance tracking, exam-risk prediction models, full spaced-
  repetition scheduling) are represented with working data models and UI
  but simplified logic — solid foundations to extend further.
