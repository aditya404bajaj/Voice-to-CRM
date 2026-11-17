# Voice-to-CRM

Record a field visit by voice. The app transcribes it, fills in the CRM fields
(customer, location, contact, date, issues, follow-up, status), and saves it
so you can search, edit, export and track follow-ups.

## Features

- Sign in / register with an **account type**: *User* (sees only their own visits and details, under **My account**) or
  *Administrator* (sees every user under **Users**, each user's full details and totals, and every visit).
  The first account created is always the administrator. Later accounts can choose Administrator only by typing the
  secret `ADMIN_SIGNUP_CODE` from `.env` (leave it empty and nobody else can sign up as administrator).
  CSRF protection, per-user data
- Record in the browser as **Audio or Video** (camera, back camera on phones), or upload audio/video files, or just type notes.
  **Add as many recordings as you like** to one visit: record, record again, upload more. Each is listed with its own
  player, and every sound track is transcribed and added to the transcript.
  Any saved visit can get more recordings later: open the visit, use **Add recording** (choose one or many files)
  or **Record another one now**. Each recording has its own **Remove recording** button.
  Supported video: mp4, webm. Each file must be under 25 MB (about 4 minutes of video), which is the speech-to-text limit;
  there is no limit on how many files a visit can have.
- Whisper speech-to-text + GPT field extraction (works without a key too: browser dictation + basic extraction)
- Visit logs: search, status/date filters, pagination, view, edit, delete, playback of every saved recording
- CSV export (respects filters, Excel/Hindi safe)
- PDF reports: one visit (with transcript) or any filtered list, via Print > Save as PDF (Hindi prints correctly)
- Customer page: every visit for a customer, contacts, locations, overdue count, "new visit for this customer"
- Email reminders: daily digest of due and overdue follow-ups (SMTP, e.g. Gmail), plus an "Email my follow-ups" button
- English / Hindi screens (switch on every page; your choice is remembered)
- Dashboard: totals, overdue and upcoming follow-ups, 14-day chart, status breakdown
- Customers page (visits per customer, open follow-ups)
- Works on phones (bottom navigation)

## Run it (Windows)

```bat
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python run.py
```

Open http://127.0.0.1:5000 and create your account. That is all: it uses SQLite
by default, so no database setup is needed.

> Microphone access works on `localhost` or `https` only.

## Use MySQL

In `.env` set `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD` and `MYSQL_DATABASE`
(for example `voice_to_crm`). Tables (and the database) are created on start.
Your old `.env` names (`MYSQL_*`, `OPENAI_API_KEY`) work unchanged.

## Turn on AI (free or paid)

**Free (Groq):** create a key at https://console.groq.com (API Keys), put it in `.env` as
`GROQ_API_KEY=gsk_...` and restart. Groq's free plan covers Whisper speech-to-text
(25 MB per file) and the field extraction.
Limits change, so check Groq's rate-limits page if you hit one.

**Paid (OpenAI):** put `OPENAI_API_KEY=sk-...` in `.env` and restart.

The provider is chosen from the keys: if `GROQ_API_KEY` is filled in, Groq is used (even if an old OpenAI key is still there). Force it with
`AI_PROVIDER=groq` or `AI_PROVIDER=openai`. Optional: `TRANSCRIBE_LANGUAGE=hi`, and the model
names can be changed with `OPENAI_TRANSCRIBE_MODEL` and `OPENAI_CHAT_MODEL`.

**No key at all** also works: record, then use browser dictation (Chrome or Edge) or type notes.
If a provider says the quota is used up, the recording is still saved and you can type the notes.

## Email reminders

1. Fill in the `SMTP_*` values in `.env` (Gmail needs an App Password) and restart.
2. Test: open the dashboard and click **Email my follow-ups**.
3. Daily emails: run `flask --app run send-reminders` once a day. Each person gets one email per day
   with their own due/overdue follow-ups (completed ones are skipped). On Windows, schedule it:

```bat
schtasks /create /tn "VoiceCRM reminders" /sc daily /st 09:00 /tr "cmd /c cd /d C:\path\to\Voice_To_CRM && venv\Scripts\flask --app run send-reminders"
```

## PDF reports

Open a visit, a customer page or the visit list and click **PDF report**, then **Print or save as PDF**
and choose "Save as PDF" as the printer. The browser draws the PDF, so Hindi text is always correct.

## Languages

Use the English / हिन्दी switch (sidebar, top bar on phones, sign-in page). Hindi text lives in
`app/translations_hi.py`; any text missing there simply shows in English. A test fails if a new
screen text is added without a Hindi translation.

## Commands

```bat
flask --app run init-db          :: create tables manually
flask --app run create-admin     :: create / reset an admin account
flask --app run cleanup-uploads  :: delete recordings never saved to a log
flask --app run send-reminders   :: email today's follow-up reminders (--force to resend)
python -m unittest discover -s tests -v
```

## Structure

```
run.py                 start the app
config.py              settings from .env
app/__init__.py        app factory, CLI, error pages
app/db.py              MySQL + SQLite layer
app/security.py        login, CSRF, security headers
app/validation.py      input checks
app/repositories/      SQL queries (users, logs)
app/services/ai.py     Whisper, extraction, offline fallback
app/services/mailer.py SMTP email;  reminders.py = daily digest
app/i18n.py            English/Hindi;  translations_hi.py = Hindi text
app/blueprints/        auth, main pages, logs, JSON api
app/templates/         HTML      app/static/  css + js
database/              schema_mysql.sql, schema_sqlite.sql
tests/                 automated tests
```

## Before going live

Set a fixed `SECRET_KEY`, `SESSION_COOKIE_SECURE=true` (with https), keep
`FLASK_DEBUG=0`, and run behind a real server, e.g. `pip install waitress` then
`waitress-serve --call app:create_app`.
